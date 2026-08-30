import subprocess
from pathlib import Path

import pytest

from app.db.models import LessonMovie
from app.services import mobile_lesson_movie
from app.services.cartoon_builder import (
    CartoonBuildError,
    _fit_final_movie_for_storage,
    _publish_final_movie,
    _run_ffmpeg_step,
    cleanup_stale_render_dirs,
    movie_render_work_root,
    reclaim_regenerable_movie_storage,
)
from app.services.mobile_lesson_movie import MOBILE_MOVIE_VERSION, MovieRenderInputs
from app.webapp.mobile_api import _queue_movie_row


def test_movie_job_schema_has_explicit_durable_lifecycle_fields():
    assert {
        "job_id", "attempt_id", "movie_version", "stage", "progress", "strategy",
        "error_code", "error_message", "attempt_count", "started_at",
        "heartbeat_at", "finished_at",
    } <= set(LessonMovie.__table__.columns.keys())
    assert MOBILE_MOVIE_VERSION == "mobile-movie-v4"


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


def test_storage_reclaim_removes_only_rebuildable_cache_and_duplicate_recorder_sources(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    cartoons = storage / "children/1/cartoons";cartoons.mkdir(parents=True)
    voices = storage / "children/1/mobile-voice/14";voices.mkdir(parents=True)
    tts = storage / "tts-cache-mobile/target";tts.mkdir(parents=True)
    localized = storage / "lesson-asset-cache";localized.mkdir(parents=True)
    final = cartoons / "old.mp4";final.write_bytes(b"existing movie")
    uploading = cartoons / "failed.mp4.uploading";uploading.write_bytes(b"partial")
    raw = voices / "voice_abc.m4a";raw.write_bytes(b"redundant recorder source")
    wav = voices / "voice_abc.wav";wav.write_bytes(b"durable db-linked voice")
    tts_file = tts / "mobile_old.ogg";tts_file.write_bytes(b"rebuildable tts")
    localized_file = localized / "english.png";localized_file.write_bytes(b"persistent localized asset")
    monkeypatch.setattr("app.services.cartoon_builder.settings.storage_root", storage)

    stats = reclaim_regenerable_movie_storage(cartoons / "new.mp4", 10**18)

    assert stats["voice_sources"] == 1 and stats["tts_cache"] == 1 and stats["uploading"] == 1
    assert not raw.exists() and not tts_file.exists() and not uploading.exists()
    assert wav.read_bytes() == b"durable db-linked voice"
    assert final.read_bytes() == b"existing movie"
    assert localized_file.read_bytes() == b"persistent localized asset"


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


def test_storage_fit_creates_verified_bounded_delivery_file(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4";source.write_bytes(b"source" * 4_000_000)
    output = tmp_path / "persistent" / "movie.mp4";output.parent.mkdir()
    work = tmp_path / "work";work.mkdir()
    monkeypatch.setattr(
        "app.services.cartoon_builder._persistent_publish_capacity",
        lambda *_args: (14_000_000, 18_000_000, 0),
    )
    monkeypatch.setattr("app.services.cartoon_builder._probe_video", lambda _path: (1280, 720, 100.0))
    monkeypatch.setattr("app.services.cartoon_builder._has_audio_stream", lambda _path: True)
    commands = []

    def fit(cmd, *, step, work, timeout):
        commands.append((cmd, step, timeout))
        Path(cmd[-1]).write_bytes(b"f" * 11_500_000)

    monkeypatch.setattr("app.services.cartoon_builder._run_ffmpeg_step", fit)
    result = _fit_final_movie_for_storage(source, output, work, 1, 60)
    assert result.name == "final-storage-fit.mp4" and result.stat().st_size == 11_500_000
    assert commands[0][1] == "storage_fit"
    assert "-map" in commands[0][0] and "0:a:0" in commands[0][0]
    assert result.stat().st_size < 14_000_000


def test_publish_storage_failure_does_not_repeat_the_full_render(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mobile_lesson_movie, "cleanup_stale_render_dirs", lambda *_args: (0, 0))
    monkeypatch.setattr(mobile_lesson_movie, "movie_storage_free_bytes", lambda _path: 500_000_000)
    monkeypatch.setattr(mobile_lesson_movie, "cartoon_text_filters", lambda *_args: [])

    def render(*_args, render_strategy, **_kwargs):
        calls.append(render_strategy)
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED",
            stage="UPLOADING",
            technical_message="delivery file does not fit",
        )

    monkeypatch.setattr(mobile_lesson_movie, "build_timeline_cartoon", render)
    with pytest.raises(CartoonBuildError) as caught:
        mobile_lesson_movie.build_mobile_lesson_movie(_inputs(tmp_path))
    assert caught.value.stage == "UPLOADING"
    assert calls == ["rich"]


def test_movie_pipeline_exposes_every_required_diagnostic_stage():
    backend = Path("app/webapp/mobile_api.py").read_text(encoding="utf-8")
    mobile = (
        Path("../DOME_MOBILE_77/src/screens/LessonPlayer.tsx").read_text(encoding="utf-8")
        + Path("../DOME_MOBILE_77/src/screens/RootApp.tsx").read_text(encoding="utf-8")
    )
    for marker in {
        "MOVIE_BUILD_REQUEST",
        "MOVIE_BUILD_REQUESTED",
        "MOVIE_BUILD_STARTED",
        "MOVIE_RECORDING_INVENTORY",
        "MOVIE_ASSETS_READY",
        "MOVIE_AUDIO_READY",
        "MOVIE_AVATAR_READY",
        "MOVIE_RENDER_STARTED",
        "MOVIE_RENDER_SUCCESS",
        "MOVIE_RENDER_FAILED",
        "MOVIE_URL_SAVED",
        "MOVIE_RETRY_STARTED",
    }:
        assert marker in backend
    assert "MOVIE_MOBILE_RECEIVED" in backend and "MOVIE_MOBILE_RECEIVED" in mobile
