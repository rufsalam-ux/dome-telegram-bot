from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.authored_content import persistent_lessons_root, bundled_lessons_root, load_authored_lesson


class HomeworkManifest(BaseModel):
    schema_version: str = "1.0"
    homework_id: str
    lesson_id: str
    course_id: str = "conversation"
    title: str = "Домашнее задание"
    description: str = ""
    enabled: bool = True
    optional: bool = True
    available_policy: str = "immediate"  # "immediate", "after_completion", "delayed"
    requires_completion_for_next_lesson: bool = False
    status: str = "published"  # "draft", "published", "archived"
    duration_minutes: int = 5
    slides: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _homework_paths(lesson_id: str) -> list[Path]:
    safe_id = str(lesson_id or "").strip().lower()
    return [
        persistent_lessons_root() / safe_id / "homework.json",
        bundled_lessons_root() / safe_id / "homework.json",
    ]


def default_homework_for_lesson(lesson_id: str, lesson_data: dict[str, Any] | None = None) -> HomeworkManifest:
    safe_id = str(lesson_id or "").strip().lower()
    ld = lesson_data or {}
    if not ld:
        try:
            ld = load_authored_lesson(safe_id)
        except Exception:
            ld = {}

    course_id = str(ld.get("course_id") or "conversation")
    lesson_title = str(ld.get("title") or safe_id)

    emb = ld.get("homework")
    if isinstance(emb, dict) and emb.get("slides"):
        return HomeworkManifest(
            homework_id=f"hw_{safe_id}",
            lesson_id=safe_id,
            course_id=course_id,
            title=str(emb.get("title") or f"{lesson_title} · Домашнее задание"),
            description=str(emb.get("description") or emb.get("summary") or ""),
            enabled=bool(emb.get("enabled", True)),
            optional=bool(emb.get("optional", True)),
            available_policy=str(emb.get("available_policy") or "immediate"),
            requires_completion_for_next_lesson=bool(emb.get("requires_completion_for_next_lesson", False)),
            status=str(emb.get("status") or "published"),
            duration_minutes=int(emb.get("duration_minutes") or 5),
            slides=list(emb.get("slides") or []),
        )

    default_slides = [
        {
            "slide_id": "hw_01_draw",
            "order": 1,
            "type": "drawing",
            "title": "Творческое задание",
            "prompt": "Нарисуй место, куда ты хотел бы отправиться, и назови три вещи, которые возьмёшь с собой.",
            "bot_says_native": "Нарисуй место, куда ты хотел бы отправиться, и назови три вещи, которые возьмёшь с собой.",
            "bot_says_target": "Draw a place you would like to travel to, and name three things you will take with you.",
            "ai_instruction": "Попроси ребёнка нарисовать путешествие и сказать ответ на английском",
            "can_skip": False,
        },
        {
            "slide_id": "hw_02_voice",
            "order": 2,
            "type": "voice_answer",
            "title": "Голосовой ответ",
            "prompt": "Назови три вещи на английском языке.",
            "bot_says_native": "Назови три вещи на английском языке.",
            "bot_says_target": "Name three things you will take with you.",
            "ai_instruction": "Послушай ответ ребёнка и похвали его за использование изучаемого языка",
            "can_skip": True,
        }
    ]

    return HomeworkManifest(
        homework_id=f"hw_{safe_id}",
        lesson_id=safe_id,
        course_id=course_id,
        title=f"{lesson_title} · Домашнее задание",
        description="Интерактивное домашнее задание после урока.",
        enabled=True,
        optional=True,
        available_policy="immediate",
        requires_completion_for_next_lesson=False,
        status="published",
        duration_minutes=5,
        slides=default_slides,
    )


def load_homework(lesson_id: str) -> HomeworkManifest:
    safe_id = str(lesson_id or "").strip().lower()
    for p in _homework_paths(safe_id):
        if p.exists():
            try:
                raw = json.loads(p.read_text("utf-8"))
                if not raw.get("homework_id"):
                    raw["homework_id"] = f"hw_{safe_id}"
                if not raw.get("lesson_id"):
                    raw["lesson_id"] = safe_id
                return HomeworkManifest.model_validate(raw)
            except Exception:
                continue
    return default_homework_for_lesson(safe_id)


def save_homework(homework: HomeworkManifest | dict[str, Any]) -> Path:
    if isinstance(homework, dict):
        manifest = HomeworkManifest.model_validate(homework)
    else:
        manifest = homework

    safe_id = str(manifest.lesson_id).strip().lower()
    target_dir = persistent_lessons_root() / safe_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "homework.json"

    temp_file = target_file.with_suffix(".json.tmp")
    temp_file.write_text(manifest.model_dump_json(indent=2) + "\n", "utf-8")
    temp_file.replace(target_file)
    return target_file


def duplicate_homework(source_lesson_id: str, target_lesson_id: str) -> HomeworkManifest:
    source_hw = load_homework(source_lesson_id)
    target_safe = str(target_lesson_id).strip().lower()
    data = source_hw.model_dump()
    data["lesson_id"] = target_safe
    data["homework_id"] = f"hw_{target_safe}"
    data["status"] = "draft"
    manifest = HomeworkManifest.model_validate(data)
    save_homework(manifest)
    return manifest


def move_homework(source_lesson_id: str, target_lesson_id: str) -> HomeworkManifest:
    manifest = duplicate_homework(source_lesson_id, target_lesson_id)
    src = load_homework(source_lesson_id)
    src.enabled = False
    save_homework(src)
    return manifest


def archive_homework(lesson_id: str) -> HomeworkManifest:
    hw = load_homework(lesson_id)
    hw.status = "archived"
    hw.enabled = False
    save_homework(hw)
    return hw
