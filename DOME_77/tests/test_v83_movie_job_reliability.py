import subprocess
from pathlib import Path

import pytest

from app.db.models import LessonMovie
from app.services import mobile_lesson_movie
from app.services.cartoon_builder import (
    CartoonBuildError,
    _publish_final_movie,
    _run_ffmpeg_step,
    cleanup_stale_render_dirs,
    movie_render_work_root,
)
from app.services.mobile_lesson_movie import MOBILE_MOVIE_VERSION, MovieRenderInputs
from app.webapp.mobile_api import _queue_movie_row


def test_movie_job_schema_has_explicit_durable_lifecycle_fields():
    assert {
        "job_id", "attempt_id", "movie_version", "stage", "progress", "strategy",
        "error_code", "error_message", "attempt_count", "started_at",
        "heartbeat_at", "finished_at",
    } <= set(LessonMovie.__table__.columns.keys())
    assert MOBILE_MOVIE_VERSION == "mobile-movie-v2"


def test_retry_preserves_durable_job_id_and_issues_a_new_attempt_id(tmp_path):
    movie=LessonMovie(lesson_session_id=12,child_id=1,lesson_id="demo_001",run_number=3,status="FAILED")
    first_job,first_attempt=_queue_movie_row(movie,tmp_path/"movie.mp4")
    movie.status="FAILED"
    second_job,second_attempt=_queue_movie_row(movie,tmp_path/"movie.mp4")
    assert second_job==first_job
    assert second_attempt!=first_attempt
    assert movie.attempt_count==2


def test_stale_work_cleanup_is_exact_and_preserves_recordings_cache_and_final(tmp_path):
    output = tmp_path / "mobile_demo_001_session11.mp4"
    output.write_bytes(b"final movie")
    voice = tmp_path / "voice.wav";voice.write_bytes(b"child voice")
    cache = tmp_path / "animation-cache";cache.mkdir();(cache / "talk.json").write_text("{}")
    stale = tmp_path / "mobile_demo_001_session11_render_deadbeef";stale.mkdir();(stale / "video_002.mp4").write_bytes(b"x" * 9000)
    other = tmp_path / "mobile_demo_001_session12_render_active";other.mkdir();(other / "video_001.mp4").write_bytes(b"y")

    removed, released = cleanup_stale_render_dirs(output)

    assert removed == 1 and released == 9000 and not stale.exists()
    assert output.read_bytes() == b"final movie" and voice.read_bytes() == b"child voice"
    assert (cache / "talk.json").exists() and other.exists()


def test_ephemeral_work_cleanup_and_streamed_final_publish_preserve_persistent_inputs(tmp_path):
    persistent = tmp_path / "persistent";persistent.mkdir()
    work_root = movie_render_work_root(tmp_path / "ephemeral")
    output = persistent / "mobile_demo_001_session12.mp4"
    voice = persistent / "voice.wav";voice.write_bytes(b"child recording")
    stale = work_root / "mobile_demo_001_session12_render_old";stale.mkdir();(stale / "window.mp4").write_bytes(b"old")
    final = work_root / "verified.mp4";final.write_bytes(b"v" * 20_000)

    removed, _released = cleanup_stale_render_dirs(output, work_root)
    _publish_final_movie(final, output, reserve_bytes=0)

    assert removed == 1 and not stale.exists()
    assert output.read_bytes() == b"v" * 20_000
    assert voice.read_bytes() == b"child recording"


def test_ffmpeg_disk_full_is_classified_without_exposing_technical_child_message(monkeypatch, tmp_path):
    def fail_disk_full(cmd, **kwargs):
        kwargs["stderr"].write(b"Task finished with error code: -28 (No space left on device)")
        kwargs["stderr"].flush()
        raise subprocess.CalledProcessError(228, cmd)

    monkeypatch.setattr(subprocess, "run", fail_disk_full)
    with pytest.raises(CartoonBuildError) as caught:
        _run_ffmpeg_step(["ffmpeg", "-version"], step="video_002", work=tmp_path, timeout=30)
    assert caught.value.code == "MOVIE_STORAGE_EXHAUSTED"
    assert caught.value.stage == "FFMPEG_RENDER"
    assert "No space left on device" in caught.value.technical_message
    assert "FFmpeg" not in str(caught.value) and "Railway" not in str(caught.value)


def _inputs(tmp_path: Path) -> MovieRenderInputs:
    base = tmp_path / "base.mp4";base.write_bytes(b"base")
    hero = tmp_path / "hero.png";hero.write_bytes(b"hero")
    voice = tmp_path / "voice.wav";voice.write_bytes(b"voice")
    return MovieRenderInputs(
        base_video=base,
        character=hero,
        audio_by_phrase={"hello": voice},
        timeline=[{"phrase_id": "hello", "visible_start": 0, "end": 2}],
        output=tmp_path / "movie.mp4",
        lesson_dir=tmp_path,
        target_language="en",
        approved_phrase_ids=("hello",),
    )


def test_render_automatically_falls_back_rich_to_safe_to_static(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mobile_lesson_movie, "cleanup_stale_render_dirs", lambda *_args: (0, 0))
    monkeypatch.setattr(mobile_lesson_movie, "movie_storage_free_bytes", lambda _path: 500_000_000)
    monkeypatch.setattr(mobile_lesson_movie, "cartoon_text_filters", lambda *_args: [])

    def render(*_args, render_strategy, **_kwargs):
        calls.append(render_strategy)
        if render_strategy != "static":
            raise CartoonBuildError(code="MOVIE_FFMPEG_FAILED", stage="FFMPEG_RENDER", technical_message=f"{render_strategy} failed")
        output = _args[4];output.write_bytes(b"ready")
        return output

    monkeypatch.setattr(mobile_lesson_movie, "build_timeline_cartoon", render)
    result = mobile_lesson_movie.build_mobile_lesson_movie(_inputs(tmp_path))
    assert result.read_bytes() == b"ready"
    assert calls == ["rich", "safe", "static"]


def test_low_storage_skips_rich_animation_but_keeps_safe_movie(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mobile_lesson_movie, "cleanup_stale_render_dirs", lambda *_args: (0, 0))
    monkeypatch.setattr(mobile_lesson_movie, "movie_storage_free_bytes", lambda _path: 80_000_000)
    monkeypatch.setattr(mobile_lesson_movie, "cartoon_text_filters", lambda *_args: [])

    def render(*_args, render_strategy, **_kwargs):
        calls.append(render_strategy);output = _args[4];output.write_bytes(b"safe");return output

    monkeypatch.setattr(mobile_lesson_movie, "build_timeline_cartoon", render)
    assert mobile_lesson_movie.build_mobile_lesson_movie(_inputs(tmp_path)).read_bytes() == b"safe"
    assert calls == ["safe"]
