import json
from copy import deepcopy
from app.core.config import settings

REMOVED_SLIDE_IDS = {"slide_02", *{f"slide_{n:02d}" for n in range(25, 40)}}
REMOVED_ORDERS = {2, *range(25, 40)}


def _runtime_slides(slides: list[dict]) -> list[dict]:
    """Hard-filter removed source slides regardless of stale JSON/state."""
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
    return slide


def load_lesson(lesson_id: str) -> dict:
    path = settings.content_root / "lessons" / lesson_id / "lesson.json"
    with path.open("r", encoding="utf-8") as f:
        lesson = json.load(f)
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
    lesson["slides"] = [_enrich_runtime_layout(slide) for slide in _runtime_slides(lesson.get("slides") or [])]
    bad = [s for s in lesson["slides"] if int(s.get("order", 0) or 0) in REMOVED_ORDERS]
    if bad:
        raise RuntimeError(f"Removed slides leaked into runtime: {[s.get('slide_id') for s in bad]}")
    lesson["runtime_revision"] = 79
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
