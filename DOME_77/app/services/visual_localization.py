from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.i18n import language_name


class VisualLocalizationError(RuntimeError):
    pass


_locks: dict[str, asyncio.Lock] = {}


def asset_has_embedded_text(source: Path) -> bool:
    stem = source.stem.lower()
    return not (stem.endswith("-clean") or stem.startswith("animal-pair-"))


def _cache_key(source: Path, target_language: str, asset_version: str) -> str:
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    material = f"{source_hash}:{target_language}:{asset_version}:{settings.openai_image_model}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


async def _request_localization_plan(source: Path, target_language: str) -> dict:
    """OCR/text-region detection plus an explicit translation map."""

    if not settings.openai_api_key:
        raise VisualLocalizationError("Image localization provider is not configured")
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    payload = {
        "model": settings.openai_text_model or "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Detect and translate text in a children's educational image. Return JSON only."},
            {"role": "user", "content": [
                {"type": "text", "text": (
                    f"Target language: {language_name(target_language)} ({target_language}). "
                    "Return has_visible_text, has_cyrillic, and text_regions. Each text_regions item must contain "
                    "source_text, translated_text, and bbox_norm [left,top,width,height]. Preserve names: Лёша=Lyosha, Мила=Mila. "
                    "Translate every instructional label; do not invent text."
                )},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"}},
            ]},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    if response.status_code >= 400:
        raise VisualLocalizationError(f"Image OCR/translation planning failed with HTTP {response.status_code}")
    try:
        plan = json.loads(response.json()["choices"][0]["message"]["content"])
        regions = list(plan.get("text_regions") or [])
    except Exception as exc:
        raise VisualLocalizationError("Image OCR/translation planning returned invalid data") from exc
    normalized = []
    for item in regions:
        source_text = str(item.get("source_text") or "").strip()
        translated_text = str(item.get("translated_text") or "").strip()
        bbox = item.get("bbox_norm") or []
        if source_text and translated_text and isinstance(bbox, list) and len(bbox) == 4:
            normalized.append({"source_text": source_text, "translated_text": translated_text, "bbox_norm": [float(value) for value in bbox]})
    return {"has_visible_text": bool(plan.get("has_visible_text")), "has_cyrillic": bool(plan.get("has_cyrillic")), "text_regions": normalized}


async def _request_localized_image(source: Path, target_language: str, plan: dict | None = None) -> bytes:
    if not settings.openai_api_key:
        raise VisualLocalizationError("Image localization provider is not configured")
    mappings = "; ".join(
        f"{item['source_text']!r} -> {item['translated_text']!r} at {item['bbox_norm']}"
        for item in (plan or {}).get("text_regions", [])
    )
    prompt = (
        "Edit this educational children's slide. Remove EVERY visible Russian/Cyrillic text element completely, "
        f"reconstruct the original background, then replace each text with a natural child-friendly translation in {language_name(target_language)}. "
        f"Use this OCR/translation map exactly: {mappings or 'detect every visible text region and translate it'}. "
        "Preserve every illustration, object, character, position, color, size and the overall composition exactly. "
        "Do not leave Russian letters or ghost text. Do not add objects, logos or captions. Keep translated text inside the original text areas. "
        "Preserve character identity exactly: Лёша is Lyosha (never Alex) and Мила is Mila. Never invent a human or animal name."
    )
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    async with httpx.AsyncClient(timeout=180) as client:
        with source.open("rb") as handle:
            files = {"image": (source.name, handle, "image/png" if source.suffix.lower() == ".png" else "image/jpeg")}
            data = {"model": settings.openai_image_model, "prompt": prompt, "size": "auto", "quality": "medium", "output_format": "png"}
            response = await client.post("https://api.openai.com/v1/images/edits", headers=headers, data=data, files=files)
        if response.status_code >= 400:
            raise VisualLocalizationError(f"Image localization failed with HTTP {response.status_code}")
        item = (response.json().get("data") or [{}])[0]
        content = b""
        if item.get("b64_json"):
            content = base64.b64decode(item["b64_json"])
        if item.get("url"):
            downloaded = await client.get(item["url"], timeout=120)
            if downloaded.status_code < 400:
                content = downloaded.content
        if content:
            await _verify_localized_image(content, target_language, client)
            return content
    raise VisualLocalizationError("Image localization provider returned no image")


async def _verify_localized_image(content: bytes, target_language: str, client: httpx.AsyncClient) -> None:
    """Fail closed if the generated asset still contains Cyrillic/wrong-language text."""
    encoded = base64.b64encode(content).decode("ascii")
    payload = {
        "model": settings.openai_text_model or "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Inspect visible text in an educational image. Return JSON only."},
            {"role": "user", "content": [
                {"type": "text", "text": f"Target language: {language_name(target_language)} ({target_language}). Return has_cyrillic and text_matches_target_language booleans. Text-free images match every target. Any Russian/Cyrillic character means has_cyrillic=true."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"}},
            ]},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=120)
    if response.status_code >= 400:
        raise VisualLocalizationError(f"Localized image verification failed with HTTP {response.status_code}")
    try:
        import json
        verdict = json.loads(response.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        raise VisualLocalizationError("Localized image verification returned invalid data") from exc
    if bool(verdict.get("has_cyrillic")) or not bool(verdict.get("text_matches_target_language")):
        raise VisualLocalizationError("Localized image failed the no-Cyrillic/target-language verification")


async def localize_embedded_text_image(
    source: Path,
    output_root: Path,
    target_language: str,
    *,
    asset_version: str = "1",
    strict: bool = True,
) -> Path:
    """Return an immutable cached visual for one asset/language/version.

    The source is never modified. Concurrent requests for the same cache key
    share one provider edit. Strict consumers never receive a Russian-text
    source as fallback for a non-Russian lesson.
    """
    if not source.exists():
        raise VisualLocalizationError("Source lesson image does not exist")
    target_language = str(target_language or "ru").lower()
    if target_language == "ru" or not asset_has_embedded_text(source):
        return source
    key = _cache_key(source, target_language, str(asset_version or "1"))
    output = output_root / "localized-visuals" / target_language / f"{source.stem}_{key}.png"
    manifest = output.with_suffix(".json")
    if output.exists() and output.stat().st_size > 1000:
        return output
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if output.exists() and output.stat().st_size > 1000:
            return output
        try:
            plan = await _request_localization_plan(source, target_language)
            # A text-free source can be reused as-is only after the OCR stage
            # explicitly reports no visible text and no Cyrillic.
            if not plan["has_visible_text"] and not plan["has_cyrillic"]:
                return source
            if plan["has_cyrillic"] and not plan["text_regions"]:
                raise VisualLocalizationError("OCR found Cyrillic but returned no renderable text regions")
            content = b""
            for generation_attempt in range(2):
                try:
                    content = await _request_localized_image(source, target_language, plan)
                    break
                except VisualLocalizationError as exc:
                    verification_failure = "verification" in str(exc).lower() or "no-cyrillic" in str(exc).lower()
                    if generation_attempt == 0 and verification_failure:
                        continue
                    raise
            if len(content) <= 1000:
                raise VisualLocalizationError("Localized image is unexpectedly empty")
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(f".{secrets.token_hex(5)}.tmp")
            temporary.write_bytes(content)
            temporary.replace(output)
            manifest_payload = {
                "cache_key": key,
                "asset_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target_language": target_language,
                "localization_version": str(asset_version or "1"),
                "pipeline": ["ocr_text_region_detection", "translation", "background_restoration_inpainting", "translated_text_render", "language_verification", "immutable_cache"],
                "text_regions": plan["text_regions"],
            }
            manifest_tmp = manifest.with_suffix(f".{secrets.token_hex(5)}.tmp")
            manifest_tmp.write_text(json.dumps(manifest_payload,ensure_ascii=False,indent=2),encoding="utf-8")
            manifest_tmp.replace(manifest)
            return output
        except Exception as exc:
            if not strict:
                return source
            if isinstance(exc, VisualLocalizationError):
                raise
            raise VisualLocalizationError(str(exc)) from exc


async def localize_embedded_russian_image(source: Path, output_root: Path, target_language: str) -> Path:
    """Compatibility entry point for Telegram rendering; mobile uses strict mode."""
    return await localize_embedded_text_image(source, output_root, target_language, asset_version="telegram-v1", strict=False)
