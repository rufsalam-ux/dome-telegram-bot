from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.config import settings
from app.services.authored_content import canonical_content_type, validate_content_lesson


AUTHORING_TYPES = {
    "info", "listen", "voice_answer", "tap_select", "multiple_choice",
    "drag_drop", "matching", "memory", "puzzle", "ordering",
    "image_hotspots", "repeat_phrase", "open_dialogue",
    "required_movie_phrase",
}

_ALLOWED_FIELDS = {
    "type", "prompt", "question", "instruction", "home_language_hint",
    "model_phrase", "model_answer_target", "answer_mode", "options",
    "correct_option_index", "correct_indices", "items", "targets", "pairs",
    "pieces", "piece_count", "image_file", "media_sequence", "hotspots",
    "requiredForMovie", "moviePhraseId", "preferredDuration",
    "sceneAssociation", "allowMultipleAttempts", "allow_skip", "max_attempts",
}


def _text(value: Any, limit: int = 800) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _kind(instruction: str) -> str:
    lowered = instruction.lower()
    rules = (
        (("memory", "мемори", "найди пары"), "memory"),
        (("puzzle", "пазл"), "puzzle"),
        (("drag", "перетащ", "полож"), "drag_drop"),
        (("match", "соедини", "сопостав"), "matching"),
        (("order", "поряд", "последователь"), "ordering"),
        (("repeat", "повтори"), "repeat_phrase"),
        (("dialog", "диалог", "поговор"), "open_dialogue"),
        (("choose", "выбери", "multiple choice"), "multiple_choice"),
        (("tap", "нажми"), "tap_select"),
    )
    for words, kind in rules:
        if any(word in lowered for word in words):
            return kind
    return "voice_answer" if any(word in lowered for word in ("say", "describe", "скажи", "опиши")) else "info"


def deterministic_proposal(instruction: str, assets: list[str] | None = None) -> dict[str, Any]:
    """Build an editable, declarative proposal without executable code."""

    prompt = _text(instruction)
    assets = [_text(item, 300) for item in (assets or []) if _text(item, 300)][:24]
    kind = _kind(prompt)
    proposal: dict[str, Any] = {
        "type": kind,
        "prompt": prompt,
        "instruction": prompt,
        "home_language_hint": "Покажи пример, если ребёнку трудно.",
        "model_phrase": "",
        "max_attempts": 3,
    }
    if assets:
        proposal["media_sequence"] = [
            {"id": f"asset_{index + 1}", "type": "image", "src": asset}
            for index, asset in enumerate(assets)
        ]
    if kind in {"drag_drop", "ordering"}:
        labels = [f"Объект {index + 1}" for index in range(max(2, min(4, len(assets) or 3)))]
        proposal["items"] = [{"id": f"item_{index + 1}", "label": label} for index, label in enumerate(labels)]
        proposal["targets"] = [{"id": f"target_{index + 1}", "label": label} for index, label in enumerate(labels)]
    elif kind in {"matching", "memory"}:
        proposal["pairs"] = [
            {"id": f"pair_{index + 1}", "left": f"Картинка {index + 1}", "right": f"Слово {index + 1}"}
            for index in range(max(2, min(8, len(assets) or 4)))
        ]
    elif kind == "puzzle":
        proposal["pieces"] = 6
        if assets:
            proposal["image_file"] = assets[0]
    elif kind in {"multiple_choice", "tap_select"}:
        proposal["options"] = ["Вариант 1", "Вариант 2", "Вариант 3"]
        proposal["correct_option_index"] = 0
    elif kind in {"voice_answer", "repeat_phrase", "open_dialogue"}:
        proposal["answer_mode"] = "optional_voice"
        proposal["model_phrase"] = "A short model answer."
    return proposal


def sanitize_proposal(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback
    clean = {key: value for key, value in raw.items() if key in _ALLOWED_FIELDS}
    kind = str(clean.get("type") or fallback.get("type") or "info").strip().lower()
    if kind not in AUTHORING_TYPES or canonical_content_type(kind) not in {
        canonical_content_type(item) for item in AUTHORING_TYPES
    }:
        clean["type"] = fallback["type"]
    clean.setdefault("prompt", fallback.get("prompt", ""))
    clean.setdefault("instruction", fallback.get("instruction", ""))
    clean.setdefault("max_attempts", 3)
    # Reuse the publication validator as the final guard. The temporary lesson
    # intentionally has exactly one reachable step.
    candidate = {
        "schema_version": "2.1", "engine": "content_v1", "lesson_id": "assistant_preview",
        "course_id": "preview", "title": "Preview", "order": 1,
        "max_completed_runs": 2, "expires_after_months": 10,
        "slides": [{"slide_id": "proposal", "order": 1, **clean}],
    }
    errors = validate_content_lesson(candidate)
    return {**clean, "validation_errors": errors}


def _response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if isinstance(content.get("text"), str):
                return content["text"]
    return ""


async def propose_task(instruction: str, assets: list[str] | None = None) -> dict[str, Any]:
    fallback = deterministic_proposal(instruction, assets)
    if not settings.openai_api_key.strip():
        return {"source": "deterministic", "proposal": sanitize_proposal(fallback, fallback)}
    request = {
        "model": settings.openai_text_model,
        "instructions": (
            "Return one JSON object configuring a DOME child lesson task. "
            f"Allowed task types: {', '.join(sorted(AUTHORING_TYPES))}. "
            f"Allowed fields: {', '.join(sorted(_ALLOWED_FIELDS))}. "
            "Never return source code, HTML, JavaScript, markdown, or executable expressions."
        ),
        "input": json.dumps({"instruction": _text(instruction), "assets": assets or []}, ensure_ascii=False),
        "text": {"format": {"type": "json_object"}},
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post("https://api.openai.com/v1/responses", headers=headers, json=request)
            response.raise_for_status()
        parsed = json.loads(_response_text(response.json()))
        return {"source": "ai", "proposal": sanitize_proposal(parsed, fallback)}
    except Exception:
        return {"source": "deterministic_fallback", "proposal": sanitize_proposal(fallback, fallback)}
