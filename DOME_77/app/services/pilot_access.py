from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import settings


def _path() -> Path:
    return Path("config") / "pilots.json"


def load_pilots() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"pilots": []}
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {"pilots": []}
    return data if isinstance(data, dict) else {"pilots": []}


def find_pilot(code: str) -> dict[str, Any] | None:
    normalized = str(code or "").strip().upper()
    if not normalized:
        return None
    for item in load_pilots().get("pilots") or []:
        if str(item.get("code") or "").strip().upper() == normalized and item.get("active", True):
            return dict(item)
    return None


def access_until(pilot: dict[str, Any]) -> datetime:
    if pilot.get("valid_until"):
        try:
            return datetime.fromisoformat(str(pilot["valid_until"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    days = max(1, int(pilot.get("duration_days", 30) or 30))
    return datetime.utcnow() + timedelta(days=days)
