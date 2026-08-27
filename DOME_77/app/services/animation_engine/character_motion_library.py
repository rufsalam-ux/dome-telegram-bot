from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


def character_key(character_png: Path) -> str:
    h = hashlib.sha256()
    with character_png.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\wа-яА-ЯёЁ]+", " ", (text or "").lower())).strip()


def signature(description_ru: str, *, speaking: bool, view: str, duration: float) -> str:
    # Bucket duration so a 4.7s and 5.0s scene can safely reuse the same 5s motion.
    bucket = 10 if duration > 5.2 else 5
    raw = f"{_norm(description_ru)}|view={view}|speaking={int(speaking)}|duration={bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class CharacterMotionLibrary:
    def __init__(self, storage_root: Path, character_png: Path, avatar_id: str | int | None = None):
        self.source_avatar_hash = character_key(character_png)
        self.avatar_id = str(avatar_id or self.source_avatar_hash)
        self.character_id = self.source_avatar_hash  # backwards-compatible runtime alias
        self.root = storage_root / "children-motion-library" / self.avatar_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "animation_library.json"
        if not self.manifest_path.exists():
            self._save(self._empty_manifest())

    def _empty_manifest(self) -> dict:
        return {"version": 2, "avatar_id": self.avatar_id, "source_avatar_hash": self.source_avatar_hash, "motions": {}}

    def _load(self) -> dict:
        try:
            data=json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(data,dict):return self._empty_manifest()
            data.update({"version":2,"avatar_id":self.avatar_id,"source_avatar_hash":self.source_avatar_hash})
            data.setdefault("motions",{})
            return data
        except Exception:
            return self._empty_manifest()

    def _save(self, data: dict) -> None:
        self.manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, sig: str) -> Path | None:
        item = (self._load().get("motions") or {}).get(sig)
        if not item:
            return None
        path = self.root / str(item.get("file") or "")
        return path if path.exists() and path.stat().st_size > 10_000 else None

    def find_compatible(self, animation_key: str, *, direction: str = "front", duration: float = 5.0,
                        transparent_background: bool = True, generation_version: str = "v1") -> Path | None:
        for sig,item in (self._load().get("motions") or {}).items():
            if (str(item.get("animation_key")) == str(animation_key)
                    and str(item.get("direction") or "front") == str(direction)
                    and abs(float(item.get("duration") or 0)-float(duration)) <= .75
                    and bool(item.get("transparent_background",True)) is bool(transparent_background)
                    and str(item.get("generation_version") or "v1") == str(generation_version)):
                found=self.find(str(sig))
                if found:return found
        return None

    def find_parameters(self, animation_key: str, *, direction: str = "front", generation_version: str = "avatar-rig-v1") -> dict | None:
        for item in (self._load().get("motions") or {}).values():
            if (str(item.get("animation_key")) == str(animation_key)
                    and str(item.get("direction") or "front") == str(direction)
                    and str(item.get("generation_version") or "v1") == str(generation_version)
                    and isinstance(item.get("rig_parameters"), dict)):
                return dict(item["rig_parameters"])
        return None

    def register_parameters(self, animation_key: str, *, rig_parameters: dict,
                            direction: str = "front", duration: float = 5.0,
                            generation_version: str = "avatar-rig-v1") -> dict:
        """Persist reusable local-rig parameters without fabricating a video file."""

        sig = hashlib.sha256(
            f"local|{animation_key}|{direction}|{generation_version}".encode("utf-8")
        ).hexdigest()[:24]
        data = self._load()
        data.setdefault("motions", {})[sig] = {
            "avatar_id": self.avatar_id,
            "source_avatar_hash": self.source_avatar_hash,
            "animation_key": animation_key,
            "direction": direction,
            "description_ru": "local reusable cutout motion",
            "speaking": animation_key == "talk",
            "view": f"side_{direction}" if direction in {"left", "right"} else "front",
            "duration": float(duration),
            "transparent_background": True,
            "asset_uri": f"rig://{self.avatar_id}/{animation_key}/{direction}",
            "rig_parameters": dict(rig_parameters),
            "generation_version": generation_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": "local_cutout",
            "file": None,
        }
        self._save(data)
        return dict(data["motions"][sig])

    def register(self, sig: str, source: Path, *, description_ru: str, speaking: bool,
                 view: str, duration: float, provider: str = "kling", animation_key: str | None = None,
                 direction: str | None = None, transparent_background: bool = True,
                 generation_version: str = "v1") -> Path:
        ext = source.suffix or ".mp4"
        target = self.root / f"motion_{sig}{ext}"
        if source.resolve() != target.resolve():
            shutil.copy2(source,target)
        data = self._load()
        data.setdefault("motions", {})[sig] = {
            "avatar_id": self.avatar_id,
            "source_avatar_hash": self.source_avatar_hash,
            "animation_key": animation_key or ("talk" if speaking else "idle"),
            "direction": direction or ("left" if str(view).endswith("left") else "right" if str(view).endswith("right") else "front"),
            "description_ru": description_ru,
            "speaking": bool(speaking),
            "view": view,
            "duration": float(duration),
            "transparent_background": bool(transparent_background),
            "asset_uri": target.name,
            "generation_version": generation_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "file": target.name,
        }
        self._save(data)
        return target
