from pathlib import Path

from app.services import storage_pressure


def test_runtime_storage_reclaims_only_regenerable_cache(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    tts = storage / "tts-cache-mobile" / "en"
    translation = storage / "translation-cache"
    child_voice = storage / "children" / "1" / "mobile-voice" / "session-7"
    authored = storage / "authored-content" / "lessons" / "demo_001"
    movie = storage / "children" / "1" / "cartoons"
    for directory in (tts, translation, child_voice, authored, movie):
        directory.mkdir(parents=True)
    (tts / "old.ogg").write_bytes(b"t" * 8)
    (translation / "old.txt").write_bytes(b"x" * 4)
    (child_voice / "take.wav").write_bytes(b"voice")
    (authored / "lesson.json").write_bytes(b"lesson")
    (movie / "final.mp4").write_bytes(b"movie")

    initial_cache_bytes = 12

    def simulated_free(_path: Path) -> int:
        remaining = sum(item.stat().st_size for root in (tts, translation) for item in root.glob("*") if item.is_file())
        return initial_cache_bytes - remaining

    monkeypatch.setattr(storage_pressure, "_free_bytes", simulated_free)
    monkeypatch.setattr(storage_pressure, "RUNTIME_CACHE_GRACE_SECONDS", 0)
    result = storage_pressure.ensure_runtime_storage_capacity(10, storage)

    assert result["ready"] is True
    assert result["files"] == 2
    assert not (tts / "old.ogg").exists()
    assert not (translation / "old.txt").exists()
    assert (child_voice / "take.wav").read_bytes() == b"voice"
    assert (authored / "lesson.json").read_bytes() == b"lesson"
    assert (movie / "final.mp4").read_bytes() == b"movie"


def test_runtime_storage_reports_pressure_when_safe_cache_is_insufficient(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    recording = storage / "children" / "1" / "mobile-voice" / "take.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"preserve-me")
    monkeypatch.setattr(storage_pressure, "_free_bytes", lambda _path: 0)

    result = storage_pressure.ensure_runtime_storage_capacity(1024, storage)

    assert result["ready"] is False
    assert result["files"] == 0
    assert recording.read_bytes() == b"preserve-me"


def test_runtime_storage_allows_sqlite_writes_below_cleanup_high_water(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(storage_pressure, "_free_bytes", lambda _path: 9_207_808)

    result = storage_pressure.ensure_runtime_storage_capacity(
        64 * 1024 * 1024,
        storage,
        minimum_free_bytes=4 * 1024 * 1024,
    )

    assert result["target_met"] is False
    assert result["minimum"] == 4 * 1024 * 1024
    assert result["after"] == 9_207_808
    assert result["ready"] is True


def test_runtime_storage_still_blocks_below_sqlite_write_minimum(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(storage_pressure, "_free_bytes", lambda _path: 3 * 1024 * 1024)

    result = storage_pressure.ensure_runtime_storage_capacity(
        64 * 1024 * 1024,
        storage,
        minimum_free_bytes=4 * 1024 * 1024,
    )

    assert result["target_met"] is False
    assert result["ready"] is False
