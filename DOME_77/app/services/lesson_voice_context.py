from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.services.conversational_tutor import TutorTurn


@dataclass(frozen=True)
class RuntimeItem:
    id: str
    label_target: str
    label_native: str

    def payload(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label_target": self.label_target,
            "label_native": self.label_native,
        }


def _ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _label(item: dict, language: str, *, fallback: str = "") -> str:
    language = str(language or "").lower()
    candidates = [
        item.get(f"label_{language}"),
        item.get("label_en") if language == "en" else None,
        item.get("label_ru") if language == "ru" else None,
        item.get("answer_value_ru") if language == "ru" else None,
        item.get("label"),
        fallback,
        item.get("id"),
    ]
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def _authored_items(slide: dict, target_language: str, native_language: str) -> list[RuntimeItem]:
    raw: list[dict] = []
    if isinstance(slide.get("drag_items"), list):
        raw = [item for item in slide["drag_items"] if isinstance(item, dict)]
    elif isinstance(slide.get("selection_options"), list):
        raw = [item for item in slide["selection_options"] if isinstance(item, dict)]
    elif isinstance(slide.get("pair"), list):
        authored_questions = {
            str(question.get("id") or question.get("correct_id") or ""): question
            for question in (slide.get("animal_questions") or [])
            if isinstance(question, dict)
        }
        raw = [
            {"id": value, "label": value, **authored_questions.get(str(value), {})}
            for value in slide["pair"]
        ]
    elif isinstance(slide.get("riddle_options"), list):
        raw = [item for item in slide["riddle_options"] if isinstance(item, dict)]
    output: list[RuntimeItem] = []
    for item in raw:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        output.append(RuntimeItem(
            id=item_id,
            label_target=_label(item, target_language, fallback=item_id),
            label_native=_label(item, native_language, fallback=item_id),
        ))
    return output


def authoritative_voice_context(
    slide: dict,
    client_context: object,
    target_language: str,
    native_language: str,
) -> dict[str, Any]:
    """Bind client interaction state to the server-authored visible item set.

    The client may report what was selected or removed, but it cannot introduce
    an item that is absent from the published lesson. The resulting structure is
    safe to include in an AI prompt and safe to use for deterministic fallbacks.
    """

    raw = client_context if isinstance(client_context, dict) else {}
    authored = _authored_items(slide, target_language, native_language)
    by_id = {item.id: item for item in authored}
    requested_visible = _ids(raw.get("visible_items"))
    visible_ids = [item_id for item_id in requested_visible if item_id in by_id] or list(by_id)
    visible_set = set(visible_ids)
    selected_ids = [item_id for item_id in _ids(raw.get("selected_items")) if item_id in visible_set]
    removed_ids = [item_id for item_id in _ids(raw.get("removed_items")) if item_id in visible_set and item_id not in selected_ids]
    task_type = str(
        slide.get("interactive_task")
        or slide.get("interaction_kind")
        or slide.get("type")
        or raw.get("task_type")
        or "voice"
    )
    selection_policy = str(
        slide.get("selection_policy")
        or ("child_choice" if task_type == "suitcase" else "authored_choice")
    )
    return {
        "task_type": task_type,
        "selection_policy": selection_policy,
        "target_language": target_language,
        "interface_language": native_language,
        "visible_items": [by_id[item_id].payload() for item_id in visible_ids],
        "selected_items": [by_id[item_id].payload() for item_id in selected_ids],
        "removed_items": [by_id[item_id].payload() for item_id in removed_ids],
        "allow_unlisted_items": False if visible_ids else True,
        "required_voice": bool(
            slide.get("requiredForMovie") is True
            or slide.get("required_for_movie") is True
            or slide.get("voice_requirement") == "required"
        ),
    }


def context_item_ids(items: Iterable[dict]) -> set[str]:
    return {str(item.get("id") or "") for item in items if isinstance(item, dict) and item.get("id")}


def contextual_assessment_goal(default_goal: str, context: dict[str, Any]) -> str:
    selected = context.get("selected_items") or []
    if not selected:
        return default_goal
    labels = ", ".join(str(item.get("label_target") or item.get("id")) for item in selected)
    policy = str(context.get("selection_policy") or "")
    task_type = str(context.get("task_type") or "")
    if policy == "child_choice":
        return (
            f"{default_goal}\nThe child freely selected: {labels}. Accept any short relevant target-language "
            "sentence about taking, liking, needing, or choosing those selected items. Do not require a hidden "
            "correct set and do not ask for any other item."
        )
    if task_type == "animal_compare":
        return (
            f"{default_goal}\nThe child selected: {labels}. Accept any short relevant target-language idea "
            "about that selected animal; a sample sentence is not an exact-string requirement."
        )
    return default_goal


def referenced_items_are_visible(result: dict, context: dict[str, Any]) -> bool:
    if context.get("allow_unlisted_items", True):
        return True
    visible = context_item_ids(context.get("visible_items") or [])
    referenced = context_item_ids(result.get("referenced_items") or []) | set(_ids(result.get("referenced_item_ids")))
    return referenced <= visible


def selected_item_turn(
    reaction_target: str,
    reaction_native: str,
    *,
    follow_up_target: str = "",
    follow_up_native: str = "",
    emotion: str = "happy",
) -> TutorTurn:
    """Create one bilingual turn from one semantic selection response."""

    return TutorTurn(
        reaction_target=reaction_target,
        reaction_native=reaction_native,
        follow_up_target=follow_up_target,
        native_hint=follow_up_native,
        emotion=emotion,
        complete=not bool(follow_up_target),
        reason="selected_item_response",
    )
