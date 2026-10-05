from __future__ import annotations

import hashlib
import json
from typing import Any


class LessonSequenceError(RuntimeError):
    """Published lesson steps do not form one deterministic runtime route."""


def step_id(step: dict[str, Any]) -> str:
    return str(step.get("slide_id") or step.get("id") or "").strip()


def next_step_id(step: dict[str, Any]) -> str:
    return str(step.get("next_slide") or step.get("next_step_id") or step.get("next") or "").strip()


def runtime_sequence(lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact stable-ID route used by mobile lesson navigation.

    Explicit links win over display order.  A cycle is a publication error, not
    something the child runtime should try to escape with an arbitrary limit.
    """

    ordered = sorted(
        [dict(item) for item in (lesson.get("slides") or lesson.get("steps") or []) if isinstance(item, dict) and item.get("enabled") is not False],
        key=lambda item: (int(item.get("order") or 9999), step_id(item)),
    )
    if not ordered:
        return []
    ids = [step_id(item) for item in ordered]
    if not all(ids) or len(ids) != len(set(ids)):
        raise LessonSequenceError("lesson runtime step IDs must be non-empty and unique")
    if not any(next_step_id(item) for item in ordered):
        return ordered
    by_id = {step_id(item): item for item in ordered}
    entry = next((item for item in ordered if item.get("entry") is True), None)
    current = step_id(entry or by_id.get("slide_01") or ordered[0])
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    while current:
        if current in seen:
            raise LessonSequenceError(f"lesson runtime route contains a cycle at {current}")
        item = by_id.get(current)
        if item is None:
            raise LessonSequenceError(f"lesson runtime route points to missing step {current}")
        seen.add(current)
        output.append(item)
        current = next_step_id(item)
    return output


def lesson_content_version(lesson: dict[str, Any]) -> str:
    """Immutable progress version for the currently published runtime route."""

    route = runtime_sequence(lesson)
    payload = {
        "lesson_id": str(lesson.get("lesson_id") or ""),
        "schema_version": str(lesson.get("schema_version") or "legacy"),
        "revision": int(lesson.get("revision") or lesson.get("runtime_revision") or 1),
        "route": route,
        "movie_phrase_ids": [
            str(item.get("phrase_id"))
            for item in (lesson.get("timeline") or [])
            if isinstance(item, dict) and str(item.get("phrase_id") or "").strip()
        ],
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    return f"{payload['lesson_id']}:r{payload['revision']}:{digest}"


def runtime_step_ids(lesson: dict[str, Any]) -> list[str]:
    return [step_id(item) for item in runtime_sequence(lesson)]


def reached_step_ids(
    lesson: dict[str, Any],
    runtime_state: dict[str, Any] | None,
    *,
    current_step_id: str | None = None,
    current_step: int | None = None,
) -> list[str]:
    """Return the durable route high-water mark plus the current route prefix.

    Recovery may move the current cursor backwards. The recorded prefix must
    therefore be merged with the cursor, never replaced by it.
    """

    route = runtime_step_ids(lesson)
    if not route:
        return []
    state = runtime_state if isinstance(runtime_state, dict) else {}
    valid = set(route)
    prior_values = state.get("reached_step_ids", [])
    reached = {
        str(value).strip()
        for value in prior_values
        if str(value).strip() in valid
    } if isinstance(prior_values, list) else set()

    cursor = str(current_step_id or "").strip()
    cursor_index = route.index(cursor) if cursor in valid else -1
    if cursor_index < 0 and current_step is not None:
        try:
            candidate = int(current_step)
            if 0 <= candidate < len(route):
                cursor_index = candidate
        except (TypeError, ValueError):
            pass
    if cursor_index >= 0:
        reached.update(route[: cursor_index + 1])
    elif not reached:
        reached.add(route[0])
    return [item for item in route if item in reached]


def record_reached_step(
    lesson: dict[str, Any], runtime_state: dict[str, Any] | None, current_step_id: str
) -> dict[str, Any]:
    state = dict(runtime_state) if isinstance(runtime_state, dict) else {}
    state["reached_step_ids"] = reached_step_ids(
        lesson, state, current_step_id=current_step_id
    )
    return state


def normal_sequence_completed(runtime_state: dict[str, Any] | None) -> bool:
    state = runtime_state if isinstance(runtime_state, dict) else {}
    return state.get("normal_sequence_completed") is True


def phrase_step_map(lesson: dict[str, Any]) -> dict[str, str]:
    """Map every authored voice slot to the stable interaction that records it."""

    result: dict[str, str] = {}
    for item in runtime_sequence(lesson):
        sid = step_id(item)
        phrase = str(item.get("required_phrase_id") or item.get("moviePhraseId") or "").strip()
        if phrase:
            result.setdefault(phrase, sid)
        for question in item.get("animal_questions") or []:
            if isinstance(question, dict):
                nested = str(question.get("phrase_id") or question.get("id") or "").strip()
                if nested:
                    result.setdefault(nested, sid)
    return result


def missing_step_payload(lesson: dict[str, Any], phrase_ids: list[str]) -> list[dict[str, str]]:
    mapping = phrase_step_map(lesson)
    return [
        {"phrase_id": phrase_id, "step_id": mapping[phrase_id]}
        for phrase_id in phrase_ids
        if phrase_id in mapping
    ]
