from __future__ import annotations
from typing import Any
from .models import MotionCommand, MotionPlan, SUPPORTED_VIEWS

ALIASES = {
    "walk_and_talk": "talk",
    "walk_from_left": "walk",
    "walk_left_then_talk": "walk",
    "walk_from_right": "walk",
    "walk_right_to_left": "walk_left",
    "walk_right_to_left_talk": "walk_left",
    "walk_left_to_right_talk": "walk_right",
    "happy_jump": "small_jump",
    "jump": "small_jump",
    "turn_to_friend": "turn_right",
    "pick_up_object": "point",
    "talk_excited": "talk",
    "stand_front_talk": "talk",
    "stand_front_listen": "listen",
    "walk_to_partner": "walk_right",
    "face_partner": "turn_right",
    "stop": "idle",
}

SEMANTIC_ACTIONS = {
    "idle", "blink", "talk", "listen", "walk_left", "walk_right",
    "turn_left", "turn_right", "wave", "point", "happy", "thinking",
    "small_jump", "enter_left", "enter_right", "exit_left", "exit_right",
    "tail_idle", "tail_sway",
    # v49 public action names remain valid for authored timelines.
    "turn", "walk", "dance", "pick_up",
}


def semantic_action(value: Any, fallback: str = "idle") -> str:
    raw = str(value or fallback).strip().lower()
    action = ALIASES.get(raw, raw)
    return action if action in SEMANTIC_ACTIONS else fallback


def _command(raw: dict[str, Any], default_start: float = 0.0) -> MotionCommand:
    action = semantic_action(raw.get("action") or raw.get("type"))
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
        action = semantic_action(legacy, "talk" if "talk" in legacy else "idle")
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


def primary_motion_action(segment: dict[str, Any]) -> str:
    plan = normalize_motion_plan(segment)
    return plan.commands[0].action if plan.commands else "idle"
