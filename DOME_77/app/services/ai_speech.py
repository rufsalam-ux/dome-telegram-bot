from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.i18n import language_name


class AISpeechError(RuntimeError):
    pass


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


async def synthesize_speech(text: str, language: str, cache_dir: Path, purpose: str) -> Path | None:
    if not settings.openai_api_key or not text:
        return None
    digest = hashlib.sha256(f"DOME_TTS_V3|{settings.openai_tts_model}|{settings.child_tts_voice}|{language}|{text}".encode()).hexdigest()[:24]
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{purpose}_{digest}.ogg"
    if output.exists() and output.stat().st_size > 0:
        return output
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
            "Never sound stern, rough, flat, robotic, cold, rushed or babyish. Keep pronunciation very clear."
        ),
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post("https://api.openai.com/v1/audio/speech", headers=headers, content=json.dumps(payload))
    if response.status_code >= 400:
        raise AISpeechError(f"Speech API error {response.status_code}: {response.text[:300]}")
    output.write_bytes(response.content)
    return output
