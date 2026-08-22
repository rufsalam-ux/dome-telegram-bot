from __future__ import annotations
import json
from pathlib import Path
from .models import CharacterRig


def load_character_rig(character_png: Path, rig_root: Path) -> CharacterRig:
    """Load a reusable rig if available; otherwise return a safe PNG fallback rig.

    A real AI segmenter/rigging service can later populate <rig_root>/<stem>/rig.json
    without changing lesson timelines or cartoon-builder APIs.
    """
    folder = rig_root / character_png.stem
    manifest = folder / "rig.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return CharacterRig(
                character_id=data.get("character_id", character_png.stem),
                root=folder,
                views=dict(data.get("views") or {}),
                parts=dict(data.get("parts") or {}),
                joints=dict(data.get("joints") or {}),
                capabilities=set(data.get("capabilities") or []),
                provider=data.get("provider", "unknown"),
                source_png=data.get("source_png") or str(character_png),
            )
        except Exception:
            pass
    return CharacterRig(
        character_id=character_png.stem,
        root=folder,
        views={"front": str(character_png)},
        capabilities={"translate", "scale", "mirror", "bob"},
        provider="fallback_png",
        source_png=str(character_png),
    )
