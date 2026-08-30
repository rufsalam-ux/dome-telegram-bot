import json
from copy import deepcopy
from pathlib import Path
from app.core.config import settings
from app.services.authored_content import (
    bundled_lessons_root,
    normalized_media_sequence,
    persistent_lessons_root,
    publication_status,
    validate_content_lesson,
)

REMOVED_SLIDE_IDS = {"slide_02", *{f"slide_{n:02d}" for n in range(25, 40)}}
REMOVED_ORDERS = {2, *range(25, 40)}


def _runtime_slides(slides: list[dict], lesson_id: str, *, content_engine: str = "") -> list[dict]:
    """Apply the historic DOME 77 cut only to its original lesson.

    Studio-authored lessons may legitimately use step_02 or orders 25..39.
    """
    if content_engine.lower() == "content_v1":
        return [slide for slide in slides if slide.get("enabled") is not False]
    if lesson_id != "demo_001":
        return list(slides)
    result = []
    for slide in slides:
        slide_id = str(slide.get("slide_id", ""))
        order = int(slide.get("order", 0) or 0)
        if slide_id in REMOVED_SLIDE_IDS or order in REMOVED_ORDERS:
            continue
        result.append(slide)
    return result


def _enrich_runtime_layout(slide: dict) -> dict:
    """Give every slide a common collision/layout contract."""

    if slide.get("interactive_task") == "suitcase":
        # Suitcase is a child-choice conversation, never a hidden scored set.
        # Normalize old persistent DOME 77 copies as well as the bundled file.
        slide["selection_policy"] = "child_choice"
        slide["follow_up_policy"] = "optional"
        slide.pop("correct_item_ids", None)
        slide.pop("incorrect_item_ids", None)
        for item in slide.get("drag_items") or []:
            if isinstance(item, dict):
                item.pop("useful", None)

    if slide.get("moviePhraseId") and not slide.get("required_phrase_id"):
        slide["required_phrase_id"] = str(slide["moviePhraseId"])
    if slide.get("voice_after_action_optional") is True:
        slide["requiredForMovie"] = False
        slide["allow_skip"] = True
        slide["unskippable"] = False
        slide["must_wait_for_answer"] = False
        slide["answer_mode"] = "optional_voice"
    if "requiredForMovie" not in slide and "required_for_movie" not in slide:
        slide["requiredForMovie"] = bool(slide.get("required_phrase_id") and slide.get("allow_skip") is False)
    if slide.get("requiredForMovie") is True or slide.get("required_for_movie") is True:
        slide["allow_skip"] = False
        slide.setdefault("answer_mode", "required_voice")

    boxes = [list(value) for value in slide.get("content_boxes", []) if isinstance(value, list) and len(value) == 4]
    for option in slide.get("selection_options", []) or []:
        rect = option.get("rect") if isinstance(option, dict) else None
        if isinstance(rect, list) and len(rect) == 4:
            boxes.append(list(rect))
    for key in ("character_box", "question_card_box", "prompt_box"):
        value = slide.get(key)
        if isinstance(value, list) and len(value) == 4:
            boxes.append(list(value))
    if not boxes:
        boxes.append(list(slide.get("visual_content_box") or [0.08, 0.06, 0.84, 0.72]))
    slide["content_boxes"] = boxes
    slide["runtime_state_machine"] = [
        "ENTER", "AI_SPEAKING", "WAITING_ACTION", "WAITING_VOICE",
        "PROCESSING", "FEEDBACK", "FOLLOW_UP", "RETRY", "COMPLETE",
    ]
    slide["media_sequence"] = normalized_media_sequence(slide)
    return slide


class LessonConfigurationError(RuntimeError):
    pass


def _candidate_paths(lesson_id: str, preview: bool) -> list[Path]:
    candidates = [persistent_lessons_root() / lesson_id / "lesson.json"]
    bundled = bundled_lessons_root() / lesson_id / "lesson.json"
    if bundled not in candidates:
        candidates.append(bundled)
    return candidates if not preview else candidates[:1] + [p for p in candidates[1:] if p.exists()]


def _read_valid_candidate(path: Path, lesson_id: str, preview: bool) -> tuple[dict, list[str]] | None:
    if not path.exists():
        return None
    try:
        lesson = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ({}, [f"invalid JSON: {exc}"])
    if str(lesson.get("engine") or "").lower() == "content_v1":
        errors = validate_content_lesson(lesson)
        if not preview and publication_status(lesson) != "PUBLISHED":
            errors.append("lesson is not published")
        if errors:
            return lesson, errors
    lesson.setdefault("lesson_id", lesson_id)
    lesson.setdefault("status", publication_status(lesson).lower())
    return lesson, []


def load_lesson(lesson_id: str, *, preview: bool = False) -> dict:
    """Load a valid published lesson, falling back from a broken live draft.

    A malformed persistent edit must never take down the production runtime.
    Admin preview may inspect a valid draft, while normal clients only receive
    published content. Bundled DOME 77 remains the last-known-good fallback.
    """

    diagnostics: list[str] = []
    selected: tuple[dict, Path] | None = None
    for path in _candidate_paths(lesson_id, preview):
        result = _read_valid_candidate(path, lesson_id, preview)
        if result is None:
            continue
        lesson, errors = result
        if not errors:
            selected = lesson, path
            break
        # An explicit persistent archive is a publication tombstone for a
        # bundled lesson. Broken edits may fall back; deliberate archive may not.
        if not preview and path.parent.parent == persistent_lessons_root() and publication_status(lesson) == "ARCHIVED":
            diagnostics.extend(f"{path}: {error}" for error in errors)
            break
        diagnostics.extend(f"{path}: {error}" for error in errors)
        if preview and path.parent.parent == persistent_lessons_root():
            break
    if selected is None:
        detail = "; ".join(diagnostics[:8]) or "lesson.json not found"
        raise LessonConfigurationError(f"Lesson {lesson_id} is unavailable: {detail}")
    lesson, path = selected
    lesson = deepcopy(lesson)
    # v51: timeline.json is the human-editable animation script for the lesson.
    # If present, it overrides the embedded legacy timeline in lesson.json.
    timeline_path = path.parent / "timeline.json"
    if timeline_path.exists():
        try:
            external_timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            if isinstance(external_timeline, list) and external_timeline:
                lesson["timeline"] = external_timeline
        except Exception:
            pass
    lesson["slides"] = [_enrich_runtime_layout(slide) for slide in _runtime_slides(
        lesson.get("slides") or [], lesson_id, content_engine=str(lesson.get("engine") or "")
    )]
    bad = [s for s in lesson["slides"] if lesson_id == "demo_001" and int(s.get("order", 0) or 0) in REMOVED_ORDERS]
    if bad:
        raise RuntimeError(f"Removed slides leaked into runtime: {[s.get('slide_id') for s in bad]}")
    lesson["runtime_revision"] = 79
    lesson["content_source"] = "persistent" if persistent_lessons_root() in path.parents else "bundled"
    lesson["publication_status"] = publication_status(lesson)
    return lesson


def validate_lesson_revision(lesson_id: str) -> list[int]:
    lesson = load_lesson(lesson_id)
    orders = [int(s.get("order", 0) or 0) for s in lesson.get("slides") or []]
    forbidden = sorted(set(orders) & REMOVED_ORDERS)
    if forbidden:
        raise RuntimeError(f"Forbidden source slides present: {forbidden}")
    if 24 in orders and 40 in orders:
        i24, i40 = orders.index(24), orders.index(40)
        if i40 != i24 + 1:
            raise RuntimeError("Interactive slide 24 must transition directly to slide 40")
    return orders
