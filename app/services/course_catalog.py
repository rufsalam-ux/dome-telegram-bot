from __future__ import annotations

import json
from pathlib import Path
from app.core.config import settings
from app.engine.schema import CourseManifest


def courses_root() -> Path:
    p = settings.content_root / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_courses() -> list[CourseManifest]:
    out: list[CourseManifest] = []
    for path in sorted(courses_root().glob("*.json")):
        try:
            out.append(CourseManifest.model_validate_json(path.read_text("utf-8")))
        except Exception:
            continue
    return out


def load_course(course_id: str) -> CourseManifest:
    path = courses_root() / f"{course_id}.json"
    if not path.exists():
        raise FileNotFoundError(course_id)
    return CourseManifest.model_validate_json(path.read_text("utf-8"))


def save_course(course: CourseManifest) -> Path:
    path = courses_root() / f"{course.course_id}.json"
    path.write_text(course.model_dump_json(indent=2), "utf-8")
    return path
