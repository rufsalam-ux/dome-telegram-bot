from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.i18n import language_name


class AISpeechError(RuntimeError):
    pass


log = logging.getLogger("dome.ai_speech")
TTS_CACHE_RESERVE_BYTES = 16 * 1024 * 1024
TTS_CACHE_ACTIVE_GRACE_SECONDS = 10 * 60


def _ephemeral_tts_dir(cache_dir: Path) -> Path:
    namespace = hashlib.sha256(str(cache_dir.resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "dome-tts-cache" / namespace


def _tts_cache_root(cache_dir: Path) -> Path:
    mobile_root = settings.storage_root / "tts-cache-mobile"
    try:
        cache_dir.resolve().relative_to(mobile_root.resolve())
    except ValueError:
        return cache_dir
    return mobile_root


def reclaim_tts_cache(cache_dir: Path, target_free_bytes: int, protected: set[Path] | None = None) -> dict[str, int]:
    """Reclaim only reproducible TTS files, never child or authored media."""

    root = _tts_cache_root(Path(cache_dir))
    protected_paths = {item.resolve() for item in (protected or set())}
    stats = {"before": 0, "after": 0, "files": 0, "bytes": 0}
    try:
        root.mkdir(parents=True, exist_ok=True)
        stats["before"] = int(shutil.disk_usage(root).free)
    except OSError:
        return stats
    if stats["before"] >= target_free_bytes:
        stats["after"] = stats["before"]
        return stats
    now = time.time()
    candidates: list[tuple[int, float, Path]] = []
    for item in root.rglob("*"):
        if not item.is_file() or item.resolve() in protected_paths:
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        incomplete = ".tmp." in item.name or item.name.endswith(".download")
        if not incomplete and now - stat.st_mtime < TTS_CACHE_ACTIVE_GRACE_SECONDS:
            continue
        candidates.append((0 if incomplete else 1, stat.st_mtime, item))
    candidates.sort(key=lambda row: (row[0], row[1], str(row[2])))
    for _priority, _mtime, item in candidates:
        try:
            if int(shutil.disk_usage(root).free) >= target_free_bytes:
                break
            size = item.stat().st_size
            item.unlink()
            stats["files"] += 1
            stats["bytes"] += size
        except (FileNotFoundError, OSError):
            continue
    try:
        stats["after"] = int(shutil.disk_usage(root).free)
    except OSError:
        stats["after"] = 0
    if stats["files"]:
        log.warning(
            "TTS_CACHE_RECLAIM root=%s before=%s after=%s target=%s files=%s bytes=%s",
            root,
            stats["before"],
            stats["after"],
            target_free_bytes,
            stats["files"],
            stats["bytes"],
        )
    return stats


def _existing_tts_path(cache_dir: Path, filename: str) -> Path | None:
    for root in (Path(cache_dir), _ephemeral_tts_dir(Path(cache_dir))):
        candidate = root / filename
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _tts_output_path(cache_dir: Path, filename: str, required_bytes: int) -> Path:
    cache_dir = Path(cache_dir)
    target_free = TTS_CACHE_RESERVE_BYTES + max(1, int(required_bytes))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        reclaim_tts_cache(cache_dir, target_free)
        if int(shutil.disk_usage(cache_dir).free) >= target_free:
            return cache_dir / filename
    except OSError as exc:
        log.warning("TTS_CACHE_PERSISTENT_UNAVAILABLE root=%s error=%s", cache_dir, exc)
    fallback = _ephemeral_tts_dir(cache_dir)
    fallback.mkdir(parents=True, exist_ok=True)
    reclaim_tts_cache(fallback, max(2 * 1024 * 1024, required_bytes + 512 * 1024))
    log.warning("TTS_CACHE_EPHEMERAL_FALLBACK persistent_root=%s fallback_root=%s", cache_dir, fallback)
    return fallback / filename


def _write_tts_atomically(output: Path, payload: bytes) -> Path:
    temporary = output.with_name(f"{output.stem}.{secrets.token_hex(5)}.tmp{output.suffix}")
    try:
        temporary.write_bytes(payload)
        temporary.replace(output)
        return output
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        if exc.errno != errno.ENOSPC:
            raise
        fallback = _ephemeral_tts_dir(output.parent)
        fallback.mkdir(parents=True, exist_ok=True)
        fallback_output = fallback / output.name
        fallback_temporary = fallback_output.with_name(f"{fallback_output.stem}.{secrets.token_hex(5)}.tmp{fallback_output.suffix}")
        try:
            fallback_temporary.write_bytes(payload)
            fallback_temporary.replace(fallback_output)
        finally:
            fallback_temporary.unlink(missing_ok=True)
        log.warning("TTS_WRITE_EPHEMERAL_FALLBACK failed_path=%s fallback_path=%s", output, fallback_output)
        return fallback_output


def _extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    raise AISpeechError("OpenAI did not return translated text")


async def translate_text(text: str, source_language: str, target_language: str) -> str:
    if not text or source_language == target_language:
        return text
    # v57: character names are identities, not words to translate. In particular
    # Лёша must never become Alex. Protect Russian forms before AI translation and
    # restore one stable Latin transliteration for non-Russian study languages.
    protected = {"Лёша":"__DOME_LYOSHA__", "Лёшу":"__DOME_LYOSHA__", "Лёшей":"__DOME_LYOSHA__"}
    translation_input = text
    if source_language == "ru" and target_language != "ru":
        for src, token in protected.items():
            translation_input = translation_input.replace(src, token)
    # persistent translation cache: known lesson text is translated once, not on every turn.
    cache_dir = settings.storage_root / "translation-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"DOME_TRANSLATION_V57|{source_language}|{target_language}|{text}".encode()).hexdigest()[:32]
    cache_file = cache_dir / f"{digest}.txt"
    if cache_file.exists():
        try:
            cached = cache_file.read_text(encoding="utf-8").strip()
            if cached:
                return cached
        except OSError:
            pass
    if not settings.openai_api_key:
        return text
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.openai_text_model,
        "instructions": (
            "Translate educational text for a child. Preserve the exact meaning, keep it concise, "
            "do not add explanations, preserve person and character names exactly (for example Mila must never become My Name), and return only the translation."
        ),
        "input": f"Translate from {language_name(source_language)} to {language_name(target_language)}:\n{translation_input}",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
    if response.status_code >= 400:
        raise AISpeechError(f"Translation API error {response.status_code}: {response.text[:300]}")
    translated = _extract_output_text(response.json())
    if target_language != "ru":
        translated = translated.replace("__DOME_LYOSHA__", "Lyosha")
        # Defensive cleanup for providers that ignore the placeholder instruction.
        translated = translated.replace("Alex", "Lyosha") if "Лёш" in text else translated
    try:
        cache_file.write_text(translated, encoding="utf-8")
    except OSError:
        pass
    return translated


async def synthesize_speech(
    text: str,
    language: str,
    cache_dir: Path,
    purpose: str,
    delivery_style: str = "warm",
) -> Path | None:
    if not settings.openai_api_key or not text:
        return None
    style = str(delivery_style or "warm").strip().lower()
    styles = {
        "happy": "Sound genuinely delighted, with a light smile and a tiny celebratory lift.",
        "curious": "Sound playfully curious; lift the intonation naturally on the single question.",
        "surprised": "Sound warmly surprised, then settle into a calm curious tone.",
        "encouraging": "Sound patient and reassuring, with a short pause before the helpful example.",
        "gentle_correction": "Correct softly and matter-of-factly; never sound disappointed.",
        "warm": "Sound warm, attentive and conversational.",
    }
    style_instruction = styles.get(style, styles["warm"])
    digest = hashlib.sha256(f"DOME_TTS_V4|{settings.openai_tts_model}|{settings.child_tts_voice}|{language}|{style}|{text}".encode()).hexdigest()[:24]
    filename = f"{purpose}_{digest}.ogg"
    cached = _existing_tts_path(cache_dir, filename)
    if cached:
        return cached
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.openai_tts_model,
        "voice": settings.child_tts_voice,
        "input": text,
        "response_format": "opus",
        "instructions": (
            f"Speak to a child learning {language_name(language)}. "
            "Use a soft, friendly, youthful feminine voice for a child. Sound kind, warm, emotionally expressive and genuinely interested. "
            "Sound like a warm, lively female children's presenter: smile in the voice, vary intonation naturally, use playful curiosity, gentle excitement, expressive pauses and a calm conversational pace. Questions should sound curious and praise should sound genuinely pleased. "
            f"{style_instruction} "
            "Never sound stern, rough, flat, robotic, cold, rushed or babyish. Keep pronunciation very clear."
        ),
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post("https://api.openai.com/v1/audio/speech", headers=headers, content=json.dumps(payload))
    if response.status_code >= 400:
        raise AISpeechError(f"Speech API error {response.status_code}: {response.text[:300]}")
    output = _tts_output_path(cache_dir, filename, len(response.content))
    return _write_tts_atomically(output, response.content)


async def synthesize_bilingual_speech(
    target_text: str,
    target_language: str,
    native_text: str,
    native_language: str,
    cache_dir: Path,
    purpose: str,
    delivery_style: str = "warm",
) -> Path | None:
    """Synthesize each language with its own voice instructions, then join it.

    Passing a Russian hint through an English TTS request makes the progressive
    PRE_A1 assistance difficult to understand.  The immutable combined cache is
    derived from both texts/languages and leaves the two source caches reusable.
    """

    target_text = str(target_text or "").strip()
    native_text = str(native_text or "").strip()
    if not target_text and not native_text:
        return None
    if native_text and native_language == target_language and native_text == target_text:
        native_text = ""
    target_audio, native_audio = await asyncio.gather(
        synthesize_speech(target_text, target_language, cache_dir / "target", f"{purpose}_target", delivery_style) if target_text else asyncio.sleep(0, result=None),
        synthesize_speech(native_text, native_language, cache_dir / "native", f"{purpose}_native", "encouraging") if native_text else asyncio.sleep(0, result=None),
    )
    if not target_audio:
        return native_audio
    if not native_audio:
        return target_audio
    digest = hashlib.sha256(
        f"DOME_BILINGUAL_TTS_V1|{target_language}|{native_language}|{delivery_style}|{target_text}|{native_text}".encode()
    ).hexdigest()[:24]
    filename = f"{purpose}_bilingual_{digest}.ogg"
    cached = _existing_tts_path(cache_dir, filename)
    if cached:
        return cached
    estimated_bytes = target_audio.stat().st_size + native_audio.stat().st_size + 512 * 1024
    output = _tts_output_path(cache_dir, filename, estimated_bytes)
    command = [
        settings.ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(target_audio),
        "-i",
        str(native_audio),
        "-filter_complex",
        "[0:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,asetpts=N/SR/TB[a0];anullsrc=r=24000:cl=mono:d=0.28[s];[1:a]aresample=24000,aformat=sample_fmts=fltp:channel_layouts=mono,asetpts=N/SR/TB[a1];[a0][s][a1]concat=n=3:v=0:a=1[out]",
        "-map",
        "[out]",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
    ]

    def _join(destination: Path) -> None:
        temporary = destination.with_name(f"{destination.stem}.{secrets.token_hex(5)}.tmp.ogg")
        local_command = [*command, str(temporary)]
        try:
            result = subprocess.run(local_command, check=False, capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AISpeechError(f"Bilingual speech assembly failed: {exc}") from exc
        try:
            if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
                detail = result.stderr.decode("utf-8", errors="replace")[-300:]
                raise AISpeechError(f"Bilingual speech assembly failed: {detail}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    try:
        await asyncio.to_thread(_join, output)
    except AISpeechError as exc:
        if "No space left on device" not in str(exc):
            raise
        fallback_root = _ephemeral_tts_dir(cache_dir)
        fallback_root.mkdir(parents=True, exist_ok=True)
        output = fallback_root / filename
        log.warning("TTS_ASSEMBLY_EPHEMERAL_RETRY failed_path=%s fallback_path=%s", cache_dir / filename, output)
        await asyncio.to_thread(_join, output)
    return output
