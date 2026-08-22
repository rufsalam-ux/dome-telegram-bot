from __future__ import annotations
from typing import Any
from .models import MotionCommand, MotionPlan, SUPPORTED_VIEWS

ALIASES = {
    "walk_and_talk": "walk",
    "walk_from_left": "walk",
    "walk_from_right": "walk",
    "happy_jump": "jump",
    "turn_to_friend": "turn",
    "pick_up_object": "pick_up",
    "talk_excited": "talk",
}


def _command(raw: dict[str, Any], default_start: float = 0.0) -> MotionCommand:
    action = str(raw.get("action") or raw.get("type") or "idle")
    action = ALIASES.get(action, action)
    view = str(raw.get("view") or "front")
    if view not in SUPPORTED_VIEWS:
        view = "front"
    params = {k: v for k, v in raw.items() if k not in {"action", "type", "start", "duration", "view"}}
    return MotionCommand(
        action=action,
        start=float(raw.get("start", default_start)),
        duration=max(0.01, float(raw.get("duration", 1.0))),
        view=view,
        params=params,
    )


def normalize_motion_plan(segment: dict[str, Any]) -> MotionPlan:
    """Normalize old v48 animation fields and new v49 action arrays into one plan."""
    raw_actions = segment.get("actions")
    commands: list[MotionCommand] = []
    if isinstance(raw_actions, list) and raw_actions:
        cursor = float(segment.get("visible_start", 0.0))
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            cmd = _command(item, cursor)
            commands.append(cmd)
            cursor = max(cursor, cmd.start + cmd.duration)
    else:
        legacy = str(segment.get("animation") or segment.get("motion") or "stand_front_talk")
        action = ALIASES.get(legacy, "talk" if "talk" in legacy else "idle")
        commands.append(MotionCommand(
            action=action,
            start=float(segment.get("visible_start", 0.0)),
            duration=max(0.01, float(segment.get("end", 1.0)) - float(segment.get("visible_start", 0.0))),
            view=str(segment.get("view") or "front"),
            params={"legacy_animation": legacy},
        ))
    return MotionPlan(
        commands=commands,
        lip_sync=bool(segment.get("lip_sync", segment.get("mouth") == "lip_sync" or segment.get("talk_start") is not None)),
        audio_path=segment.get("audio_path"),
        fallback_action=str(segment.get("animation") or "stand_front_talk"),
    )
