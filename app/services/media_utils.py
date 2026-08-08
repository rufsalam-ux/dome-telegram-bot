from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.core.config import settings


class MediaProbeError(RuntimeError):
    pass


def media_duration_seconds(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        return float(payload["format"]["duration"])
    except (FileNotFoundError, subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProbeError(f"Не удалось определить длительность файла {path.name}") from exc
