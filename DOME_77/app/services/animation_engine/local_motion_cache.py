from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.animation_library import animation_profile
from .character_motion_library import CharacterMotionLibrary
from .motion_planner import SEMANTIC_ACTIONS


LOCAL_MOTION_VERSION = "avatar-cutout-v2"


def analyze_hero_for_animation(metadata: dict[str, Any] | None, *, built_in: bool = False) -> dict[str, Any]:
    """Choose the richest safe mode without making animation a movie prerequisite."""

    payload = metadata or {}
    capabilities = component_capabilities(payload)
    rig = payload.get("rigMetadata") if isinstance(payload.get("rigMetadata"), dict) else {}
    trusted = built_in or str(payload.get("source") or "").upper() == "CATALOG" or rig.get("trusted") is True
    moving_limbs = sum(bool(capabilities[key]) for key in ("canMoveLeftArm","canMoveRightArm","canMoveLeftLeg","canMoveRightLeg"))
    rich_face = capabilities["canBlink"] and capabilities["canAnimateMouth"] and capabilities["canMoveHead"]
    if trusted and rich_face and moving_limbs >= 2:
        mode = "FULL_RIG"
    elif rich_face or moving_limbs or capabilities["canAnimateTail"] or capabilities["canMoveHead"]:
        mode = "PARTIAL_RIG"
    elif payload:
        mode = "SIMPLE_CHARACTER_MOTION"
    else:
        mode = "STATIC_COMPOSITE"
    return {"mode":mode,"capabilities":capabilities,"trusted":trusted,"static_fallback":True}


def component_capabilities(metadata: dict[str, Any] | None) -> dict[str, bool]:
    payload = metadata or {}
    rig = payload.get("rigMetadata") if isinstance(payload.get("rigMetadata"), dict) else {}
    raw = rig.get("capabilities") if isinstance(rig.get("capabilities"), dict) else {}
    return {
        "canBlink": bool(raw.get("canBlink") or raw.get("blink")),
        "canAnimateMouth": bool(raw.get("canAnimateMouth")),
        "canMoveHead": bool(raw.get("canMoveHead")),
        "canMoveLeftArm": bool(raw.get("canMoveLeftArm")),
        "canMoveRightArm": bool(raw.get("canMoveRightArm")),
        "canMoveLeftLeg": bool(raw.get("canMoveLeftLeg")),
        "canMoveRightLeg": bool(raw.get("canMoveRightLeg")),
        "canAnimateTail": bool(raw.get("canAnimateTail") or raw.get("tailMotion")),
    }


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
    capabilities = component_capabilities(metadata)
    can_gesture = capabilities["canMoveLeftArm"] or capabilities["canMoveRightArm"]
    can_walk = capabilities["canMoveLeftLeg"] or capabilities["canMoveRightLeg"]
    return {
        "action": action,
        "direction": action_direction(action),
        "rotation": min(float(profile.get("rotation", 0.006)), .012 if fallback else .04),
        "body_bob": min(float(profile.get("body_bob", profile.get("walk_bob", 1.5))), 2.0 if fallback else 8.0),
        "walk_bob": 0.0 if fallback else float(profile.get("walk_bob", 0.0)),
        "blink_period": float(profile.get("blink_period", 0.0)) if capabilities["canBlink"] else 0.0,
        "mouth_pulse": bool(profile.get("mouth_pulse", False)) and capabilities["canAnimateMouth"],
        "gesture": profile.get("gesture") if can_gesture else None,
        "limb_cycle": bool(profile.get("limb_cycle", False)) and can_walk,
        "tail_sway": float(profile.get("tail_sway", 0.0)) if capabilities["canAnimateTail"] else 0.0,
        "capabilities": capabilities,
        "whole_body_fallback": fallback,
    }


def ensure_local_motion_cache(character_png: Path, storage_root: Path, metadata: dict[str, Any] | None = None,
                              *, avatar_id: str | int | None = None) -> tuple[CharacterMotionLibrary, int, int]:
    """Cache reusable rig parameters once per avatar and return hit/create counts."""

    library = CharacterMotionLibrary(storage_root, character_png, avatar_id=avatar_id)
    actions = set(SEMANTIC_ACTIONS)
    if not ((metadata or {}).get("tailPoint") or component_capabilities(metadata)["canAnimateTail"]):
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
