from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_VIEWS = ("front", "three_quarter_left", "three_quarter_right", "side_left", "side_right", "back")

@dataclass(slots=True)
class CharacterRig:
    character_id: str
    root: Path
    views: dict[str, str] = field(default_factory=dict)
    parts: dict[str, str] = field(default_factory=dict)
    joints: dict[str, Any] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)
    provider: str = "fallback_png"
    source_png: str | None = None

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

@dataclass(slots=True)
class MotionCommand:
    action: str
    start: float = 0.0
    duration: float = 1.0
    view: str = "front"
    params: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class MotionPlan:
    commands: list[MotionCommand] = field(default_factory=list)
    lip_sync: bool = False
    audio_path: str | None = None
    fallback_action: str = "stand_front_talk"
