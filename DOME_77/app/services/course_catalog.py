from __future__ import annotations

import json
from pathlib import Path
from app.core.config import settings
from app.engine.schema import CourseManifest
from app.services.authored_content import augment_course
from app.services.runtime_mode import client_course_allowed


def bundled_courses_root() -> Path:
    """Read-only courses shipped with the application."""
    p = settings.content_root / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def persistent_courses_root() -> Path:
    """Courses authored in Content Studio.

    Keeping authoring data under ``storage_root`` makes it survive a deploy and
    lets it override (but never mutate) a bundled course with the same id.
    """
    p = settings.storage_root / "authored-content" / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def course_roots() -> list[Path]:
    return [persistent_courses_root(), bundled_courses_root()]


def courses_root() -> Path:
    """Backward-compatible write target for newly authored courses."""
    return persistent_courses_root()


def list_courses() -> list[CourseManifest]:
    out: list[CourseManifest] = []
    seen: set[str] = set()
    for root in course_roots():
        for path in sorted(root.glob("*.json")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                raw = augment_course(json.loads(path.read_text("utf-8")))
                if not client_course_allowed(raw.get("course_id")):
                    raw["active"] = False
                out.append(CourseManifest.model_validate(raw))
            except Exception:
                continue
    return out


def load_course(course_id: str) -> CourseManifest:
    for root in course_roots():
        path = root / f"{course_id}.json"
        if not path.exists():
            continue
        raw = augment_course(json.loads(path.read_text("utf-8")))
        if not client_course_allowed(raw.get("course_id")):
            raw["active"] = False
        return CourseManifest.model_validate(raw)
    raise FileNotFoundError(course_id)


def save_course(course: CourseManifest) -> Path:
    path = courses_root() / f"{course.course_id}.json"
    path.write_text(course.model_dump_json(indent=2), "utf-8")
    return path
