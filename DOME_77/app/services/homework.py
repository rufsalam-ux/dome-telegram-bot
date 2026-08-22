from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.platform_settings import load_settings


def _extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def _fallback(child, attempts: list) -> str:
    recent = []
    for a in attempts:
        txt = (getattr(a, "transcript", None) or "").strip()
        if txt and txt not in recent:
            recent.append(txt)
        if len(recent) >= 3:
            break
    target = child.target_language or "изучаемый язык"
    lines = [
        f"1. Скажи вслух 3 короткие фразы на языке {target} по теме сегодняшнего урока.",
        "2. Повтори 3–5 слов или выражений, которые сегодня были сложнее всего.",
        "3. Придумай одну новую фразу с одним из этих слов.",
    ]
    if recent:
        lines[1] = "2. Повтори и немного измени одну из сегодняшних фраз: " + "; ".join(recent[:2])
    return "\n".join(lines)


def _lesson_homework(lesson_id: str) -> dict | None:
    folder = settings.content_root / "lessons" / lesson_id
    for filename in ("manifest.json", "lesson.json"):
        path = folder / filename
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        hw = data.get("homework")
        if isinstance(hw, dict):
            return hw
    return None


def _manual_homework_text(hw: dict) -> str:
    parts: list[str] = []
    instructions = str(hw.get("instructions") or "").strip()
    if instructions:
        parts.append(instructions)
    activities = hw.get("activities") or []
    numbered = []
    for item in activities:
        text = str(item.get("instruction") or "").strip()
        if text:
            numbered.append(text)
    if numbered:
        if parts:
            parts.append("")
        parts.extend(f"{i}. {text}" for i, text in enumerate(numbered, 1))
    return "\n".join(parts).strip()


async def generate_ai_homework(child, attempts: list, lesson_title: str = "") -> str:
    fallback = _fallback(child, attempts)
    if not settings.openai_api_key:
        return fallback
    evidence = []
    for a in attempts[-12:]:
        evidence.append({
            "text": getattr(a, "transcript", None),
            "status": getattr(a, "status", None),
            "grammar_errors": getattr(a, "grammar_errors", None),
            "pronunciation_errors": getattr(a, "pronunciation_errors", None),
            "semantic_match": getattr(a, "semantic_match", None),
        })
    instructions = (
        "Create OPTIONAL homework for a learner after a DOME language lesson. "
        "Use only the supplied lesson evidence. 1 to 3 very short tasks, total 3-10 minutes. "
        "Match the learner level and age if known. Make it encouraging, not compulsory. "
        "Prefer speaking, vocabulary, short reading, matching or a tiny game. "
        "Never invent a weakness that is not supported by evidence. Return only the homework text in Russian."
    )
    payload = {"model": settings.openai_text_model, "instructions": instructions, "input": json.dumps({
        "lesson": lesson_title, "age": getattr(child, "age_years", None), "level": child.language_level,
        "target_language": child.target_language, "evidence": evidence
    }, ensure_ascii=False)}
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
        if r.status_code < 400:
            text = _extract_output_text(r.json())
            if text:
                return text
    except Exception:
        pass
    return fallback


async def resolve_homework(child, attempts: list, lesson_id: str) -> tuple[str | None, int, dict]:
    """Return (homework_text, duration_minutes, homework_config).

    Manual homework in the lesson manifest always wins. AI generation runs only when
    the lesson explicitly sets source=ai_auto. If the lesson has no homework or it is
    disabled, None is returned and nothing is sent.
    """
    global_cfg = load_settings("homework")
    if not global_cfg.get("enabled", True):
        return None, 0, {}
    hw = _lesson_homework(lesson_id)
    if not hw or not hw.get("enabled", False):
        return None, 0, hw or {}
    # Global switches are hard limits; per-lesson settings can only narrow them.
    hw = dict(hw)
    hw["send_to_bot"] = bool(global_cfg.get("send_to_bot", True) and hw.get("send_to_bot", True))
    hw["send_to_parent_email"] = bool(global_cfg.get("send_to_parent_email", True) and hw.get("send_to_parent_email", True))
    hw["allow_skip"] = bool(global_cfg.get("allow_skip", True) and hw.get("allow_skip", True))
    hw["allow_defer"] = bool(global_cfg.get("allow_defer", True) and hw.get("allow_defer", True))
    hw["keep_in_archive"] = bool(global_cfg.get("keep_in_archive", True) and hw.get("keep_in_archive", True))
    max_duration = max(1, int(global_cfg.get("max_duration_minutes", 10) or 10))
    duration = max(1, min(max_duration, int(hw.get("duration_minutes", global_cfg.get("default_duration_minutes", 5)) or 5)))
    source = str(hw.get("source") or "manual")
    if source == "ai_auto":
        text = await generate_ai_homework(child, attempts, lesson_title=lesson_id)
    else:
        text = _manual_homework_text(hw)
    if not text:
        return None, duration, hw
    return text, duration, hw


# Backward compatibility for old imports/tests.
async def generate_homework(child, attempts: list, lesson_title: str = "") -> str:
    return await generate_ai_homework(child, attempts, lesson_title)
