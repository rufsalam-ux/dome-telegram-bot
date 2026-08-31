from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from app.core.config import settings


log = logging.getLogger("dome.storage_pressure")

# Keep enough space for SQLite WAL/journal writes and normal request metadata.
# Large movie intermediates already use ephemeral storage; these directories
# contain only reproducible AI output and are safe to rebuild on demand.
RUNTIME_STORAGE_RESERVE_BYTES = 64 * 1024 * 1024
RUNTIME_CACHE_GRACE_SECONDS = 60
REGENERABLE_CACHE_NAMES = ("tts-cache-mobile", "tts-cache", "translation-cache")


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _cache_candidates(root: Path, now: float) -> list[tuple[int, float, Path]]:
    candidates: list[tuple[int, float, Path]] = []
    if not root.is_dir():
        return candidates
    try:
        items = root.rglob("*")
        for item in items:
            try:
                if not item.is_file():
                    continue
                stat = item.stat()
            except OSError:
                continue
            incomplete = ".tmp." in item.name or item.name.endswith((".tmp", ".download", ".uploading"))
            recent = now - stat.st_mtime < RUNTIME_CACHE_GRACE_SECONDS
            # Incomplete files go first, then inactive cache entries. Recent
            # complete files are an emergency-only last resort.
            priority = 0 if incomplete else (2 if recent else 1)
            candidates.append((priority, stat.st_mtime, item))
    except OSError:
        return candidates
    return candidates


def ensure_runtime_storage_capacity(
    target_free_bytes: int = RUNTIME_STORAGE_RESERVE_BYTES,
    storage_root: Path | None = None,
) -> dict[str, int | bool]:
    """Reserve database write space by pruning only regenerable caches.

    Child profiles, lesson progress, recordings, movies, authored content,
    localized visual assets and avatar files are deliberately outside the
    allowlist and can never be selected by this function.
    """

    root = Path(storage_root or settings.storage_root)
    target = max(1, int(target_free_bytes))
    result: dict[str, int | bool] = {
        "before": 0,
        "after": 0,
        "target": target,
        "files": 0,
        "bytes": 0,
        "ready": False,
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        result["before"] = _free_bytes(root)
    except OSError as exc:
        log.error("RUNTIME_STORAGE_CHECK_FAILED root=%s error=%s", root, exc)
        return result
    if int(result["before"]) >= target:
        result["after"] = result["before"]
        result["ready"] = True
        return result

    candidates: list[tuple[int, float, Path]] = []
    now = time.time()
    for cache_name in REGENERABLE_CACHE_NAMES:
        candidates.extend(_cache_candidates(root / cache_name, now))
    candidates.sort(key=lambda row: (row[0], row[1], str(row[2])))

    for _priority, _mtime, item in candidates:
        try:
            if _free_bytes(root) >= target:
                break
            size = item.stat().st_size
            item.unlink()
            result["files"] = int(result["files"]) + 1
            result["bytes"] = int(result["bytes"]) + int(size)
        except (FileNotFoundError, OSError):
            continue

    try:
        result["after"] = _free_bytes(root)
    except OSError:
        result["after"] = 0
    result["ready"] = int(result["after"]) >= target
    log.warning(
        "RUNTIME_STORAGE_RECLAIM root=%s before=%s after=%s target=%s files=%s bytes=%s ready=%s",
        root,
        result["before"],
        result["after"],
        target,
        result["files"],
        result["bytes"],
        result["ready"],
    )
    return result
