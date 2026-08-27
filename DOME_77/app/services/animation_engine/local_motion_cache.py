from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.animation_library import animation_profile
from .character_motion_library import CharacterMotionLibrary
from .motion_planner import SEMANTIC_ACTIONS


LOCAL_MOTION_VERSION = "avatar-rig-v1"


def safe_fallback_required(metadata: dict[str, Any] | None) -> bool:
    payload = metadata or {}
    rig = payload.get("rigMetadata") if isinstance(payload.get("rigMetadata"), dict) else {}
    capabilities = rig.get("capabilities") if isinstance(rig.get("capabilities"), dict) else {}
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return bool(capabilities.get("safeWholeBodyFallback")) or confidence < .72


def action_direction(action: str) -> str:
    if action.endswith("_left") or action == "walk_left":
        return "left"
    if action.endswith("_right") or action == "walk_right":
        return "right"
    return "front"


def local_motion_parameters(action: str, root: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = animation_profile(action, root)
    fallback = safe_fallback_required(metadata)
    return {
        "action": action,
        "direction": action_direction(action),
        "rotation": min(float(profile.get("rotation", 0.006)), .012 if fallback else .04),
        "body_bob": min(float(profile.get("body_bob", profile.get("walk_bob", 1.5))), 2.0 if fallback else 8.0),
        "walk_bob": 0.0 if fallback else float(profile.get("walk_bob", 0.0)),
        "blink_period": float(profile.get("blink_period", 0.0)),
        "mouth_pulse": bool(profile.get("mouth_pulse", False)),
        "gesture": None if fallback else profile.get("gesture"),
        "limb_cycle": False if fallback else bool(profile.get("limb_cycle", False)),
        "tail_sway": 0.0 if fallback else float(profile.get("tail_sway", 0.0)),
        "whole_body_fallback": fallback,
    }


def ensure_local_motion_cache(character_png: Path, storage_root: Path, metadata: dict[str, Any] | None = None,
                              *, avatar_id: str | int | None = None) -> tuple[CharacterMotionLibrary, int, int]:
    """Cache reusable rig parameters once per avatar and return hit/create counts."""

    library = CharacterMotionLibrary(storage_root, character_png, avatar_id=avatar_id)
    actions = set(SEMANTIC_ACTIONS)
    if not (metadata or {}).get("tailPoint"):
        actions -= {"tail_idle", "tail_sway"}
    hits = created = 0
    profile_root = storage_root / "animation-library"
    for action in sorted(actions):
        direction = action_direction(action)
        if library.find_parameters(action, direction=direction, generation_version=LOCAL_MOTION_VERSION) is not None:
            hits += 1
            continue
        library.register_parameters(
            action,
            direction=direction,
            duration=5.0,
            generation_version=LOCAL_MOTION_VERSION,
            rig_parameters=local_motion_parameters(action, profile_root, metadata),
        )
        created += 1
    return library, hits, created
