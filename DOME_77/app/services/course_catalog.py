from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.engine.schema import CourseManifest
from app.services.authored_content import augment_course
from app.services.runtime_mode import client_course_allowed


def bundled_courses_root() -> Path:
    p = settings.content_root / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def persistent_courses_root() -> Path:
    p = settings.storage_root / "authored-content" / "courses"
    p.mkdir(parents=True, exist_ok=True)
    return p


def courses_root() -> Path:
    return persistent_courses_root()


def _course_file(course_id: str) -> Path | None:
    safe_id = str(course_id or "").strip().lower()
    persistent = persistent_courses_root() / f"{safe_id}.json"
    if persistent.exists():
        return persistent
    bundled = bundled_courses_root() / f"{safe_id}.json"
    if bundled.exists():
        return bundled
    return None


def list_courses(for_client: bool = False) -> list[CourseManifest]:
    seen: set[str] = set()
    out: list[CourseManifest] = []

    # 1. Scan persistent courses first (authored overrides bundled)
    for path in sorted(persistent_courses_root().glob("*.json")):
        cid = path.stem.lower()
        if cid in seen:
            continue
        seen.add(cid)
        try:
            raw = augment_course(json.loads(path.read_text("utf-8")))
            manifest = CourseManifest.model_validate(raw)
            if for_client:
                if not manifest.active or manifest.locked or manifest.status != "published":
                    continue
                if not client_course_allowed(manifest.course_id):
                    continue
            out.append(manifest)
        except Exception:
            continue

    # 2. Scan bundled courses
    for path in sorted(bundled_courses_root().glob("*.json")):
        cid = path.stem.lower()
        if cid in seen:
            continue
        seen.add(cid)
        try:
            raw = augment_course(json.loads(path.read_text("utf-8")))
            manifest = CourseManifest.model_validate(raw)
            if for_client:
                if not manifest.active or manifest.locked or manifest.status != "published":
                    continue
                if not client_course_allowed(manifest.course_id):
                    continue
            out.append(manifest)
        except Exception:
            continue

    # Sort by order, then title
    out.sort(key=lambda c: (int(getattr(c, "order", 1) or 1), str(c.title or c.course_id)))
    return out


def load_course(course_id: str) -> CourseManifest:
    path = _course_file(course_id)
    if not path or not path.exists():
        raise FileNotFoundError(f"Course not found: {course_id}")
    raw = augment_course(json.loads(path.read_text("utf-8")))
    return CourseManifest.model_validate(raw)


def save_course(course: CourseManifest | dict[str, Any]) -> Path:
    if isinstance(course, dict):
        manifest = CourseManifest.model_validate(course)
    else:
        manifest = course

    target_path = persistent_courses_root() / f"{manifest.course_id}.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(".json.tmp")
    temp_path.write_text(manifest.model_dump_json(indent=2) + "\n", "utf-8")
    temp_path.replace(target_path)
    return target_path


def create_course(
    course_id: str,
    title: str,
    description: str = "",
    cover_image: str = "",
    order: int = 1,
    active: bool = True,
    status: str = "draft",
) -> CourseManifest:
    safe_id = str(course_id).strip().lower()
    if _course_file(safe_id) is not None:
        raise ValueError(f"Course with ID '{safe_id}' already exists")

    manifest = CourseManifest(
        course_id=safe_id,
        title=title.strip(),
        description=description.strip(),
        cover_image=cover_image.strip(),
        order=int(order),
        active=bool(active),
        locked=False,
        status=status if status in {"draft", "published", "archived"} else "draft",
        lesson_ids=[],
    )
    save_course(manifest)
    return manifest


def update_course(course_id: str, updates: dict[str, Any]) -> CourseManifest:
    existing = load_course(course_id)
    data = existing.model_dump()
    for key, val in updates.items():
        if key in data and key != "course_id":
            data[key] = val
    manifest = CourseManifest.model_validate(data)
    save_course(manifest)
    return manifest


def duplicate_course(source_id: str, target_id: str, new_title: str | None = None) -> CourseManifest:
    source = load_course(source_id)
    target_safe = str(target_id).strip().lower()
    if _course_file(target_safe) is not None:
        raise ValueError(f"Course '{target_safe}' already exists")

    data = source.model_dump()
    data["course_id"] = target_safe
    data["title"] = (new_title or f"{source.title} (копия)").strip()
    data["status"] = "draft"
    manifest = CourseManifest.model_validate(data)
    save_course(manifest)
    return manifest


def archive_course(course_id: str) -> CourseManifest:
    return update_course(course_id, {"status": "archived", "active": False})


def delete_course(course_id: str, force: bool = False) -> dict[str, Any]:
    safe_id = str(course_id).strip().lower()
    manifest = load_course(safe_id)

    if not force and len(manifest.lesson_ids) > 0:
        archive_course(safe_id)
        return {"action": "archived", "reason": f"Курс содержит {len(manifest.lesson_ids)} уроков. Выполнен безопасный перевод в архив."}

    p_path = persistent_courses_root() / f"{safe_id}.json"
    deleted = False
    if p_path.exists():
        p_path.unlink()
        deleted = True
    b_path = bundled_courses_root() / f"{safe_id}.json"
    if b_path.exists():
        archive_course(safe_id)
        return {"action": "archived", "reason": "Встроенный базовый курс переведен в архив."}

    return {"action": "deleted" if deleted else "archived"}


def reorder_courses(ordered_ids: list[str]) -> list[CourseManifest]:
    courses = {c.course_id: c for c in list_courses()}
    out: list[CourseManifest] = []
    for order, cid in enumerate(ordered_ids, start=1):
        if cid in courses:
            c = courses[cid]
            c.order = order
            save_course(c)
            out.append(c)
    return out


def add_lesson_to_course(course_id: str, lesson_id: str) -> CourseManifest:
    course = load_course(course_id)
    lid = str(lesson_id).strip()
    if lid not in course.lesson_ids:
        course.lesson_ids.append(lid)
        save_course(course)
    return course


def remove_lesson_from_course(course_id: str, lesson_id: str) -> CourseManifest:
    course = load_course(course_id)
    lid = str(lesson_id).strip()
    if lid in course.lesson_ids:
        course.lesson_ids.remove(lid)
        save_course(course)
    return course
