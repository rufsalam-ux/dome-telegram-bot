from __future__ import annotations

import hashlib
import json
import re
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
    def __init__(self, storage_root: Path, character_png: Path):
        self.character_id = character_key(character_png)
        self.root = storage_root / "children-motion-library" / self.character_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "animation_library.json"
        if not self.manifest_path.exists():
            self._save({"version": 1, "character_key": self.character_id, "motions": {}})

    def _load(self) -> dict:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "character_key": self.character_id, "motions": {}}

    def _save(self, data: dict) -> None:
        self.manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def find(self, sig: str) -> Path | None:
        item = (self._load().get("motions") or {}).get(sig)
        if not item:
            return None
        path = self.root / str(item.get("file") or "")
        return path if path.exists() and path.stat().st_size > 10_000 else None

    def register(self, sig: str, source: Path, *, description_ru: str, speaking: bool,
                 view: str, duration: float, provider: str = "kling") -> Path:
        ext = source.suffix or ".mp4"
        target = self.root / f"motion_{sig}{ext}"
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        data = self._load()
        data.setdefault("motions", {})[sig] = {
            "description_ru": description_ru,
            "speaking": bool(speaking),
            "view": view,
            "duration": float(duration),
            "provider": provider,
            "file": target.name,
        }
        self._save(data)
        return target
