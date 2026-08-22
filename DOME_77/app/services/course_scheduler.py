from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from app.core.config import settings
from app.services.authored_content import augment_course


def _load_course(course_id: str) -> dict:
    path = settings.content_root / "courses" / f"{course_id}.json"
    try:
        return augment_course(json.loads(path.read_text("utf-8")))
    except Exception:
        return {}


def _md(value: str) -> tuple[int, int]:
    month, day = str(value).split("-", 1)
    return int(month), int(day)


def _in_window(today: date, start_md: str, end_md: str) -> bool:
    sm, sd = _md(start_md)
    em, ed = _md(end_md)
    current = (today.month, today.day)
    start = (sm, sd)
    end = (em, ed)
    if start <= end:
        return start <= current <= end
    # Cross-year windows, e.g. 12-01 .. 02-28.
    return current >= start or current <= end


def active_seasons(course: dict, today: date | None = None) -> list[dict]:
    today = today or datetime.now().date()
    seasons = list((course.get("seasonal") or {}).get("periods") or [])
    active = []
    for item in seasons:
        if not item.get("enabled", True):
            continue
        try:
            if _in_window(today, item.get("start", "01-01"), item.get("end", "12-31")):
                active.append(item)
        except Exception:
            continue
    # Explicit priority wins; otherwise the order entered in Control Center wins.
    return sorted(active, key=lambda x: int(x.get("priority", 100)))


def choose_next_lesson(course_id: str, completed_lesson_ids: Iterable[str], today: date | None = None) -> str | None:
    """Choose the next unseen lesson.

    v41 seasonal rules:
    * With one new lesson per week, active seasonal lessons stay first.
    * With more than one new lesson per week, alternate: seasonal -> normal ->
      seasonal -> normal while both queues still contain unseen lessons.
    * When one queue is exhausted, continue the other queue.
    * Outside seasonal windows, follow the normal master course order.

    ``completed_lesson_ids`` may be an ordered iterable.  When it is ordered,
    the most recently completed lesson is used to decide which side of the
    seasonal alternation comes next.
    """
    course = _load_course(course_id)
    order = [str(x) for x in course.get("lesson_ids") or []]
    completed_sequence = [str(x) for x in completed_lesson_ids]
    completed = set(completed_sequence)
    if not order:
        return None

    seasons = active_seasons(course, today)
    if seasons:
        # A lesson can belong to more than one active period. Treat every active
        # seasonal lesson as thematic for the alternation.
        active_seasonal_ids: set[str] = set()
        for season in seasons:
            active_seasonal_ids.update(str(x) for x in season.get("lesson_ids") or [])

        unseen_seasonal = [x for x in order if x in active_seasonal_ids and x not in completed]
        unseen_normal = [x for x in order if x not in active_seasonal_ids and x not in completed]
        lessons_per_week = max(1, int(course.get("lessons_per_week", 1) or 1))

        if lessons_per_week > 1 and unseen_seasonal and unseen_normal:
            # Start an active season with a thematic lesson. Thereafter alternate
            # according to the last completed lesson that belongs to the course.
            last_completed = next((x for x in reversed(completed_sequence) if x in order), None)
            if last_completed in active_seasonal_ids:
                return unseen_normal[0]
            return unseen_seasonal[0]

        if unseen_seasonal:
            return unseen_seasonal[0]
        if unseen_normal:
            return unseen_normal[0]

    for lesson_id in order:
        if lesson_id not in completed:
            return lesson_id
    return None


def course_for_lesson(lesson_id: str) -> str | None:
    courses_dir = settings.content_root / "courses"
    for path in sorted(courses_dir.glob("*.json")):
        try:
            data = augment_course(json.loads(path.read_text("utf-8")))
        except Exception:
            continue
        if lesson_id in (data.get("lesson_ids") or []):
            return data.get("course_id") or path.stem
    return None


def first_active_course_id() -> str | None:
    courses_dir = settings.content_root / "courses"
    for path in sorted(courses_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if data.get("active", True) and data.get("lesson_ids"):
            return data.get("course_id") or path.stem
    return None
