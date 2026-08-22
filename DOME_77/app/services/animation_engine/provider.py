from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from .models import CharacterRig, MotionPlan

class AnimationProvider(ABC):
    """Provider interface for future AI/2D skeletal/video animation backends."""
    name = "abstract"

    @abstractmethod
    def can_render(self, rig: CharacterRig, plan: MotionPlan) -> bool: ...

    @abstractmethod
    def render_alpha_clip(self, rig: CharacterRig, plan: MotionPlan, output_path: Path) -> Path: ...

class NoopProvider(AnimationProvider):
    """Placeholder used until a production AI rig/motion provider is configured."""
    name = "noop"
    def can_render(self, rig: CharacterRig, plan: MotionPlan) -> bool:
        return False
    def render_alpha_clip(self, rig: CharacterRig, plan: MotionPlan, output_path: Path) -> Path:
        raise RuntimeError("No full-body animation provider is configured")
