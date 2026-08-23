import hashlib
import asyncio
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import (
    Base,
    Character,
    Child,
    LessonEntitlement,
    LessonMovie,
    LessonSession,
    Parent,
    VoiceAttempt,
)
from app.services import lesson_access, mobile_lesson_movie
from app.services.cartoon_builder import _probe_video, _resolve_normalized_timeline
from app.services.mobile_lesson_movie import (
    MovieRenderInputs,
    build_mobile_lesson_movie,
    required_movie_phrase_ids,
    select_movie_voice_takes,
)
from app.services.mobile_tokens import issue_session_token
from app.webapp import mobile_api


ROOT = Path(__file__).resolve().parents[1]
LESSON_PATH = ROOT / "content/lessons/demo_001/lesson.json"
MOBILE_PLAYER = ROOT.parent / "DOME_MOBILE_77/src/screens/LessonPlayer.tsx"
MOBILE_INTERACTIONS = ROOT.parent / "DOME_MOBILE_77/src/data/lessonInteractions.ts"


def load_lesson():
    return json.loads(LESSON_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_m1_is_the_verified_production_base_and_timeline_is_normalized():
    lesson = load_lesson()
    base = LESSON_PATH.parent / lesson["cartoon_base"]
    assert lesson["cartoon_base"] == "M-1.mp4"
    assert base.stat().st_size == 86_876_453
    assert sha256_file(base) == lesson["video_reference"]["base"]["sha256"]
    assert {key:lesson["video_reference"]["base"][key] for key in ("width","height","duration")} == {"width": 1920, "height": 1080, "duration": 99.73}

    expected = [
        ("lesha_clothes", 21, 24, 31), ("mila_gift", 39, 39, 44),
        ("take_trip", 54, 54, 59), ("polar_bear", 64, 64, 69),
        ("lion", 70, 70, 75), ("parrot", 75, 75, 80),
        ("giraffe", 80, 80, 85), ("penguin", 85, 85, 90),
        ("zebra", 90, 90, 95), ("invite", 95, 95, 100),
    ]
    assert [(row["phrase_id"], row["visible_start"], row["talk_start"], row["end"]) for row in lesson["timeline"]] == expected
    for row in lesson["timeline"]:
        assert 0 <= row["floor_y_norm"] <= 1
        assert 0 < row["height_norm"] <= 1
        for key in ("x_norm", "x_start_norm", "x_end_norm"):
            if key in row:
                assert -0.25 <= row[key] <= 1.05
        assert "x" not in row and "y" not in row and "height" not in row
    assert json.loads((LESSON_PATH.parent / "timeline.json").read_text(encoding="utf-8")) == lesson["timeline"]


def test_normalized_positions_resolve_to_floor_aligned_pixels():
    timeline = load_lesson()["timeline"]
    resolved = _resolve_normalized_timeline(timeline, 1920, 1080)
    for authored, pixels in zip(timeline, resolved):
        assert pixels["height"] == round(authored["height_norm"] * 1080)
        assert pixels["y"] + pixels["height"] == round(authored["floor_y_norm"] * 1080)


def test_mobile_interactions_cover_selection_suitcase_animals_audio_level_and_mood():
    player = MOBILE_PLAYER.read_text(encoding="utf-8")
    interactions = MOBILE_INTERACTIONS.read_text(encoding="utf-8")
    lesson = load_lesson()
    by_id = {slide["slide_id"]: slide for slide in lesson["slides"]}

    # Reusable hotspots now live in the backend-authored lesson definition;
    # only compatibility/animal rectangles remain in the mobile module.
    assert interactions.count("rect:{left:") >= 10
    assert len(by_id["slide_09"]["selection_options"]) == 6
    assert len(by_id["slide_20"]["selection_options"]) == 4
    assert "selectedCardBranch" in player and "selected_card_id" in player and "card_question_index" in player
    assert "DragDropSuitcase" in player and "updateSuitcase" in player and "persistInteraction" in player
    drag = (ROOT.parent / "DOME_MOBILE_77/src/components/DragDropSuitcase.tsx").read_text(encoding="utf-8")
    assert "PanResponder.create" in drag and "onPanResponderMove" in drag and "scrollEnabled={!dragging}" in player
    assert "onPress={()=>toggleSuitcase" not in player
    assert "penguin_parrot" in interactions and "lion_turtle" in interactions
    assert "lesson-target-${slide.slide_id}-${option.id}" in player
    assert "useAudioPlayerStatus" in player and "stage==='AI_SPEAKING'" in player
    assert "runtimePrompt(slide,languageLevel,workingDifficulty,'initial')" in player
    assert "correction_target" in player and "advance_allowed" in (ROOT / "app/webapp/mobile_api.py").read_text(encoding="utf-8")
    assert "MOOD_EMOJIS.map" in player and "completed:true" in player
    assert "activeAnimalQuestion" in player and "isGift" in player and "WAITING_ACTION" in player
    assert [row["phrase_id"] for row in by_id["slide_46"]["animal_questions"]] == ["penguin","parrot"]
    assert any(str(value).lower().startswith("спокой") for value in by_id["slide_49"]["mood_options"])
    assert by_id["slide_45"]["image"].endswith("slide-45-clean.png") and "!isRiddle||riddleRevealed" in player
    assert by_id["slide_20"]["image"].endswith("slide-20-repaired.png")


def test_suitcase_mobile_assets_are_exact_authored_transparent_crops():
    lesson = load_lesson();slide = next(row for row in lesson["slides"] if row["slide_id"] == "slide_24")
    root = ROOT.parent / "DOME_MOBILE_77/assets/lesson/demo_001/suitcase-authored"
    assert slide["drag_source_asset"] == "lesson-images/slide-24.png"
    assert {row["id"] for row in slide["drag_items"]} == {"jacket","binoculars","water","compass","teddy","camera","telescope","fish","notebook","sunglasses"}
    for item in slide["drag_items"]:
        image = Image.open(root / item["asset"])
        assert image.mode == "RGBA" and image.getextrema()[3][0] == 0 and image.getextrema()[3][1] == 255
    assert (root / "suitcase-target.png").exists()


def test_red_parrot_uses_verified_visual_ground_truth():
    ground_truth = json.loads((LESSON_PATH.parent / "visual_ground_truth.json").read_text(encoding="utf-8"))
    parrot = ground_truth["animals"]["parrot"]
    assert parrot["verified"] is True
    assert parrot["primary_color"] == "red"
    assert (LESSON_PATH.parent / parrot["canonical_asset"]).exists()


def test_only_latest_accepted_real_take_is_selected_for_each_movie_phrase(tmp_path):
    lesson = load_lesson();phrases = required_movie_phrase_ids(lesson)
    attempts = []
    for index, phrase in enumerate(phrases):
        path = tmp_path / f"{phrase}.wav";path.write_bytes((phrase * 20).encode())
        attempts.append(SimpleNamespace(phrase_id=phrase, status="ACCEPTED_CORRECT", audio_path=str(path)))
        if index == 0:
            replacement = tmp_path / f"{phrase}_new.wav";replacement.write_bytes(b"new accepted take")
            attempts.append(SimpleNamespace(phrase_id=phrase, status="ACCEPTED_WITH_SUPPORT", audio_path=str(replacement)))
    extra = tmp_path / "warmup.wav";extra.write_bytes(b"warmup")
    attempts.append(SimpleNamespace(phrase_id="slide_01", status="ACCEPTED_CORRECT", audio_path=str(extra)))
    selected, missing = select_movie_voice_takes(attempts, lesson)
    assert missing == [] and list(selected) == phrases
    assert selected[phrases[0]].name.endswith("_new.wav")
    assert "slide_01" not in selected


@pytest.mark.asyncio
async def test_completion_and_movie_job_are_idempotent(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    hero = tmp_path / "hero.png";Image.new("RGBA", (128, 256), (255, 0, 0, 255)).save(hero)
    lesson = load_lesson();render_calls = []
    async with sessions() as db:
        parent = Parent(email="movie@example.com", password_hash="hash", email_verified=True, email_reports_enabled=False)
        db.add(parent);await db.flush()
        child = Child(parent_id=parent.id, display_name="Movie Child", target_language="en", native_language="ru")
        db.add(child);await db.flush()
        character = Character(child_id=child.id, original_path=str(hero), processed_path=str(hero), status="READY", source="CATALOG")
        db.add(character);await db.flush();child.active_character_id=character.id
        entitlement = LessonEntitlement(child_id=child.id, lesson_id="demo_001", course_id="conversation", max_completed_runs=2, completed_runs=0, source="FREE_DEMO")
        session = LessonSession(child_id=child.id, lesson_id="demo_001", status="IN_PROGRESS")
        db.add_all([entitlement, session]);await db.flush()
        for phrase in required_movie_phrase_ids(lesson):
            audio = tmp_path / f"{phrase}.wav";audio.write_bytes(b"real child voice")
            db.add(VoiceAttempt(lesson_session_id=session.id, phrase_id=phrase, attempt_number=1, audio_path=str(audio), status="ACCEPTED_CORRECT"))
        await db.commit();parent_id, session_id = parent.id, session.id

    def fake_render(inputs):
        render_calls.append(inputs)
        inputs.output.parent.mkdir(parents=True, exist_ok=True);inputs.output.write_bytes(b"0" * 20_000)
        return inputs.output

    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "build_mobile_lesson_movie", fake_render)
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "mobile_auth_secret", "test-secret-that-is-long-enough-for-mobile-auth")
    token = issue_session_token(parent_id);headers = {"Authorization": f"Bearer {token}"}
    app = web.Application();mobile_api.register_mobile_routes(app);client = TestClient(TestServer(app));await client.start_server()
    try:
        first = await client.post(f"/api/mobile/session/{session_id}/complete", headers=headers, json={})
        assert first.status == 200
        first_payload=await first.json();assert first_payload["movie_status"] in {"PROCESSING","READY"}
        # Await the registered background job rather than racing the shared
        # Windows executor with an arbitrary wall-clock polling deadline.
        pending=[task for task in mobile_api._movie_tasks if not task.done()]
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending),timeout=10)
        for _ in range(20):
            status_response=await client.get(f"/api/mobile/session/{session_id}/movie",headers=headers)
            status_payload=await status_response.json()
            if status_payload["status"]=="READY":break
            await asyncio.sleep(0.01)
        assert status_payload["status"]=="READY" and status_payload["url"]
        second = await client.post(f"/api/mobile/session/{session_id}/complete", headers=headers, json={})
        assert (await second.json())["movie_status"] == "READY"
    finally:
        await client.close()
    async with sessions() as db:
        assert await db.scalar(select(func.count(LessonMovie.id))) == 1
        assert await db.scalar(select(func.count(LessonSession.id)).where(LessonSession.status == "COMPLETED")) == 1
        entitlement = await db.scalar(select(LessonEntitlement));assert entitlement.completed_runs == 1
    assert len(render_calls) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupted_movie_job_becomes_idempotently_retryable(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        parent = Parent(email="restart@example.com", password_hash="hash", email_verified=True)
        db.add(parent);await db.flush()
        child = Child(parent_id=parent.id, display_name="Restart Child")
        db.add(child);await db.flush()
        session = LessonSession(child_id=child.id, lesson_id="demo_001", status="COMPLETED")
        db.add(session);await db.flush()
        db.add(LessonMovie(lesson_session_id=session.id, child_id=child.id, lesson_id="demo_001", run_number=1, status="PROCESSING", output_path=str(tmp_path / "missing.mp4")))
        await db.commit()
    monkeypatch.setattr(mobile_lesson_movie, "SessionLocal", sessions)
    assert await mobile_lesson_movie.recover_interrupted_mobile_movie_jobs() == 1
    async with sessions() as db:
        movie = await db.scalar(select(LessonMovie))
        assert movie.status == "FAILED" and "idempotent retry" in movie.error
    await engine.dispose()


@pytest.mark.skipif(os.getenv("DOME_RUN_MOVIE_E2E") != "1", reason="set DOME_RUN_MOVIE_E2E=1 for the real 100-second FFmpeg render")
def test_real_happy_path_renders_m1_to_mp4(tmp_path, monkeypatch):
    ffmpeg = os.getenv("DOME_FFMPEG_BIN") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("DOME_FFMPEG_BIN/ffmpeg is required for the movie E2E test")
    monkeypatch.setattr(settings, "ffmpeg_bin", ffmpeg)
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    hero = tmp_path / "hero.png";image = Image.new("RGBA", (300, 520), (0, 0, 0, 0));draw = ImageDraw.Draw(image);draw.ellipse((70, 10, 230, 170), fill=(255, 180, 90, 255));draw.rectangle((105, 165, 195, 450), fill=(30, 120, 235, 255));image.save(hero)
    voices = {}
    for phrase in required_movie_phrase_ids(load_lesson()):
        path = tmp_path / f"{phrase}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16_000);stream.writeframes(b"\x00\x00" * 16_000)
        voices[phrase] = path
    output = tmp_path / "happy-path.mp4";lesson = load_lesson()
    result = build_mobile_lesson_movie(MovieRenderInputs(base_video=LESSON_PATH.parent / "M-1.mp4", character=hero, audio_by_phrase=voices, timeline=lesson["timeline"], output=output, lesson_dir=LESSON_PATH.parent, target_language="en"))
    assert result == output and output.stat().st_size > 100_000
    width,height,duration=_probe_video(output)
    assert (width,height)==(1920,1080) and duration>=99.9
