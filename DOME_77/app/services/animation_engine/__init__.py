"""DOME v49 extensible character animation architecture.

The engine is deliberately provider-neutral. It can use a future AI rig/motion provider,
while preserving the v48 single-PNG renderer as a safe fallback.
"""
from .models import CharacterRig, MotionCommand, MotionPlan
from .rig_loader import load_character_rig
from .motion_planner import normalize_motion_plan

__all__ = ["CharacterRig", "MotionCommand", "MotionPlan", "load_character_rig", "normalize_motion_plan"]

from .runtime_provider import prepare_character_animation
