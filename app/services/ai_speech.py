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
    if not settings.openai_api_key:
        return text
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.openai_text_model,
        "instructions": (
            "Translate educational text for a child. Preserve the exact meaning, keep it concise, "
            "do not add explanations, and return only the translation."
        ),
        "input": f"Translate from {language_name(source_language)} to {language_name(target_language)}:\n{text}",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
    if response.status_code >= 400:
        raise AISpeechError(f"Translation API error {response.status_code}: {response.text[:300]}")
    return _extract_output_text(response.json())


async def synthesize_speech(text: str, language: str, cache_dir: Path, purpose: str) -> Path | None:
    if not settings.openai_api_key or not text:
        return None
    digest = hashlib.sha256(f"{settings.openai_tts_model}|{settings.openai_tts_voice}|{language}|{text}".encode()).hexdigest()[:24]
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{purpose}_{digest}.ogg"
    if output.exists() and output.stat().st_size > 0:
        return output
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.openai_tts_model,
        "voice": settings.openai_tts_voice,
        "input": text,
        "response_format": "opus",
        "instructions": (
            f"Speak to a child learning {language_name(language)}. "
            "Sound lively, playful, warm and encouraging, like an excellent energetic children's teacher. "
            "Use expressive intonation, a smiling voice, gentle excitement and natural conversational rhythm. "
            "Keep pronunciation very clear; never sound robotic, flat, rushed or babyish."
        ),
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post("https://api.openai.com/v1/audio/speech", headers=headers, content=json.dumps(payload))
    if response.status_code >= 400:
        raise AISpeechError(f"Speech API error {response.status_code}: {response.text[:300]}")
    output.write_bytes(response.content)
    return output
