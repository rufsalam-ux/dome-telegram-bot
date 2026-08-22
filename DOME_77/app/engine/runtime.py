from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.activity_registry import REGISTRY
from app.engine.schema import LessonManifest, ActivitySpec


@dataclass(frozen=True)
class RuntimeDecision:
    activity_id: str
    activity_type: str
    renderer: str
    supported: bool
    waits_for_answer: bool
    allow_skip: bool
    required: bool
    reason: str = ""


def load_manifest(path: Path) -> LessonManifest:
    return LessonManifest.model_validate_json(path.read_text("utf-8"))


def decide(activity: ActivitySpec) -> RuntimeDecision:
    spec = REGISTRY[activity.type]
    supported = bool(spec.implemented_now)
    return RuntimeDecision(
        activity_id=activity.id,
        activity_type=activity.type,
        renderer=spec.renderer,
        supported=supported,
        waits_for_answer=activity.waits_for_answer,
        allow_skip=activity.allow_skip,
        required=activity.required,
        reason="" if supported else "renderer_not_implemented_yet",
    )


def normalize_activity(raw: dict[str, Any], index: int) -> dict[str, Any]:
    out = dict(raw)
    out.setdefault("id", f"activity_{index+1:03d}")
    out.setdefault("type", "speak")
    out.setdefault("instruction", "")
    out.setdefault("required", False)
    out.setdefault("allow_skip", not out["required"])
    out.setdefault("max_attempts", 3)
    out.setdefault("waits_for_answer", True)
    out.setdefault("target_language_required", False)
    out.setdefault("config", {})
    return out


def migrate_manifest_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Make authored lesson data forward-compatible without changing semantics."""
    out = dict(data)
    out["schema_version"] = "2.1"
    activities = out.get("activities") or []
    out["activities"] = [normalize_activity(a, i) for i, a in enumerate(activities)]
    out.setdefault("metadata", {})
    out["metadata"].setdefault("adaptive", True)
    out["metadata"].setdefault("feedback_default", "gentle")
    out["metadata"].setdefault("low_confidence_is_not_error", True)
    return out


def validate_manifest_dict(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        manifest = LessonManifest.model_validate(migrate_manifest_dict(data))
    except Exception as exc:
        return [str(exc)]
    ids = set()
    for a in manifest.activities:
        if a.id in ids:
            errors.append(f"duplicate activity id: {a.id}")
        ids.add(a.id)
        if a.required and a.allow_skip:
            errors.append(f"{a.id}: required activity cannot allow skip")
        if a.max_attempts < 1:
            errors.append(f"{a.id}: max_attempts must be >= 1")
        if a.camera.enabled and a.camera.coordinate_frame == "child" and not a.camera.auto_detect_mirror:
            errors.append(f"{a.id}: child-relative camera tasks should auto-detect mirror")
    return errors


def save_migrated(path: Path) -> LessonManifest:
    raw = json.loads(path.read_text("utf-8"))
    migrated = migrate_manifest_dict(raw)
    errors = validate_manifest_dict(migrated)
    if errors:
        raise ValueError("; ".join(errors))
    path.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), "utf-8")
    return LessonManifest.model_validate(migrated)
