from __future__ import annotations
import json
from pathlib import Path
from .models import CharacterRig


def load_character_rig(character_png: Path, rig_root: Path, metadata: dict | None = None) -> CharacterRig:
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
                capability_map={key: bool(value) for key, value in (data.get("capability_map") or {}).items()},
                provider=data.get("provider", "unknown"),
                source_png=data.get("source_png") or str(character_png),
            )
        except Exception:
            pass
    rig_metadata=(metadata or {}).get("rigMetadata") if isinstance((metadata or {}).get("rigMetadata"),dict) else {}
    capabilities=rig_metadata.get("capabilities") if isinstance(rig_metadata.get("capabilities"),dict) else {}
    enabled={name for name,value in capabilities.items() if value is True}
    return CharacterRig(
        character_id=character_png.stem,
        root=folder,
        views={"front": str(character_png)},
        joints=dict(rig_metadata.get("joints") or {}),
        capabilities={"translate", "scale", "mirror", "bob", *enabled},
        capability_map={name: bool(value) for name, value in capabilities.items()},
        provider="metadata_cutout" if rig_metadata else "fallback_png",
        source_png=str(character_png),
    )
