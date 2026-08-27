import hashlib
import asyncio
import base64
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
    InteractiveResult,
    LessonMovie,
    LessonSession,
    MovieVoiceSlot,
    Parent,
    VoiceAttempt,
)
from app.services import lesson_access, mobile_lesson_movie
from app.services.audio_processing import VoiceActivity
from app.services.conversational_tutor import TutorTurn
from app.services.cartoon_builder import AVATAR_PERCEPTUAL_SCALE, _probe_video, _render_windows, _resolve_normalized_timeline, _shift_timed_filters
from app.services.mobile_lesson_movie import (
    MovieRenderInputs,
    build_mobile_lesson_movie,
    load_movie_contract,
    required_movie_phrase_ids,
    select_movie_voice_takes,
)
from app.services.mobile_tokens import issue_session_token
from app.services.speech_pipeline import SpeechAssessment
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
    assert lesson["cartoon_base"] == "M1-canonical-20260810.mov"
    assert base.stat().st_size == 90_830_739
    assert sha256_file(base) == lesson["video_reference"]["base"]["sha256"]
    assert {key:lesson["video_reference"]["base"][key] for key in ("width","height","duration")} == {"width": 1920, "height": 1080, "duration": 99.71}
    contract = load_movie_contract("demo_001", lesson)
    assert contract.base_video == base.resolve()
    assert contract.expected_base_sha256 == "6659ae0f495b658cab4ef3048b0f58095c0252f786f4c0b8c25ecfafc88b7210"
    assert contract.audio_policy == lesson["movie_audio_policy"]

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
        assert pixels["height"] == round(authored["height_norm"] * AVATAR_PERCEPTUAL_SCALE * 1080)
        assert pixels["y"] + pixels["height"] == round(authored["floor_y_norm"] * 1080)


def test_movie_renderer_partitions_the_full_timeline_into_bounded_windows():
    timeline = _resolve_normalized_timeline(load_lesson()["timeline"], 1920, 1080)
    windows = _render_windows(timeline, 100.0)
    assert windows[0] == (0.0, 21.0) and windows[-1] == (95.0, 100.0)
    assert max(
        sum(float(item["visible_start"]) < end and float(item["end"]) > start for item in timeline)
        for start, end in windows
    ) == 1
    shifted = _shift_timed_filters(["drawbox=enable='between(t,39.0,44.0)'"], 39.0)
    assert shifted == ["drawbox=enable='between(t,0.000,5.000)'"]
    builder = (ROOT / "app/services/cartoon_builder.py").read_text(encoding="utf-8")
    assert "TemporaryDirectory" in builder and "video_concat" in builder
    assert "adelay=" not in builder and "split=10" not in builder


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
    assert "activeCardQuestion" in player and "selected_card_id" in player and "card_question_index" in player
    assert "DragDropSuitcase" in player and "updateSuitcase" in player and "persistInteraction" in player
    drag = (ROOT.parent / "DOME_MOBILE_77/src/components/DragDropSuitcase.tsx").read_text(encoding="utf-8")
    assert "PanResponder.create" in drag and "onPanResponderMove" in drag and "scrollEnabled={!dragging}" in player
    assert "collapsable={false}" in drag and "suitcaseDropAccepted" in drag and "Отпусти здесь" in drag
    assert "suitcase-tap-fallback-" in drag and "packed_items:next" in player
    assert "onPress={()=>toggleSuitcase" not in player
    assert "penguin_parrot" in interactions and "lion_turtle" in interactions
    assert "lesson-target-${slide.slide_id}-${option.id}" in player
    assert "useAudioPlayerStatus" in player and "stage==='AI_SPEAKING'" in player
    assert "runtimePrompt(slide,languageLevel,workingDifficulty,'initial')" in player
    assert "correction_target" in player and "advance_allowed" in (ROOT / "app/webapp/mobile_api.py").read_text(encoding="utf-8")
    assert "MOOD_EMOJIS.map" in player and "completed:true" in player
    assert "Повторить сборку" in player and "completed.movie_error" not in player
    assert "FFmpeg" not in player and "Railway Logs" not in player
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
async def test_scripted_mobile_demo_traverses_real_endpoints_and_reaches_ready_movie(monkeypatch, tmp_path):
    """A production-contract QA run: action/voice/progress/complete/movie."""
    engine=create_async_engine("sqlite+aiosqlite:///:memory:");sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    hero=tmp_path/"hero.png";Image.new("RGBA",(160,280),(0,0,0,0)).save(hero)
    async with sessions() as db:
        parent=Parent(email="scripted-e2e@example.com",password_hash="hash",email_verified=True,email_reports_enabled=False);db.add(parent);await db.flush()
        child=Child(parent_id=parent.id,display_name="Scripted Child",target_language="en",native_language="ru",language_level="PRE_A1");db.add(child);await db.flush()
        character=Character(child_id=child.id,original_path=str(hero),processed_path=str(hero),status="READY",source="CATALOG");db.add(character);await db.flush();child.active_character_id=character.id
        db.add(LessonEntitlement(child_id=child.id,lesson_id="demo_001",course_id="conversation",max_completed_runs=2,completed_runs=0,source="FREE_DEMO"));await db.commit();parent_id,child_id=parent.id,child.id

    def fake_activity(_path):return VoiceActivity(2.0,1.4,0.7,-24.0,-8.0,True,"SPEECH_DETECTED")
    def fake_prepare(_raw,wav,_max_sec):wav.write_bytes(b"RIFF"+b"child-voice"*200);return wav
    async def fake_assess(_wav,_target,_native,goal,_accepted,_attempt,*_args,**_kwargs):
        return SpeechAssessment(transcript=f"answer to {goal}",detected_language="en",confidence=0.99,semantic_match=1.0,status="ACCEPTED_CORRECT",response_target=f"You answered {goal}",tutor_turn=TutorTurn(reaction_target=f"You answered {goal}",emotion="happy",complete=True,reason="accepted"))
    def fake_render(inputs):inputs.output.parent.mkdir(parents=True,exist_ok=True);inputs.output.write_bytes(b"rendered-from-M-1"*2000);return inputs.output

    monkeypatch.setattr(mobile_api,"SessionLocal",sessions);monkeypatch.setattr(lesson_access,"SessionLocal",sessions)
    monkeypatch.setattr(mobile_api,"analyze_voice_activity",fake_activity);monkeypatch.setattr(mobile_api,"prepare_child_voice",fake_prepare);monkeypatch.setattr(mobile_api,"assess_speech",fake_assess);monkeypatch.setattr(mobile_api,"build_mobile_lesson_movie",fake_render)
    monkeypatch.setattr(settings,"storage_root",tmp_path/"storage");monkeypatch.setattr(settings,"mobile_auth_secret","scripted-e2e-secret-that-is-long-enough");monkeypatch.setattr(settings,"openai_api_key","")
    token=issue_session_token(parent_id);headers={"Authorization":f"Bearer {token}"};app=web.Application();mobile_api.register_mobile_routes(app);client=TestClient(TestServer(app));await client.start_server()
    lesson=load_lesson();by_id={slide["slide_id"]:slide for slide in lesson["slides"]};audio=base64.b64encode(b"real-recording"*200).decode()
    async def post_interactive(session_id,slide_id,task,result):
        response=await client.post(f"/api/mobile/session/{session_id}/interactive",headers=headers,json={"slide_id":slide_id,"task_type":task,"result":result});assert response.status==200
    async def post_voice(session_id,slide_id,phrase_id,prompt):
        response=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json={"audio_base64":audio,"slide_id":slide_id,"phrase_id":phrase_id,"prompt":prompt});assert response.status==200;payload=await response.json();assert payload["accepted"] is True and payload["tutor_turn"]["reason"]=="accepted"
    try:
        started=await client.post("/api/mobile/session/start",headers=headers,json={"child_id":child_id,"lesson_id":"demo_001"});assert started.status==200;session_id=(await started.json())["session_id"]
        await post_interactive(session_id,"slide_09","card_selector",{"selected_card_id":"A","card_question_index":0,"completed":False})
        for index,question in enumerate(by_id["slide_09"]["card_question_sets"]["A"]):
            await post_voice(session_id,"slide_09",f"slide_09:A:{question['id']}",question.get("pre_a1_text") or question["text"])
            await post_interactive(session_id,"slide_09","card_selector",{"selected_card_id":"A","card_question_index":index+1,"completed":index==2})
        await post_voice(session_id,"slide_19","lesha_clothes","Why are you dressed so warmly?")
        await post_interactive(session_id,"slide_20","gift_selector",{"selected_gift_id":"book"});await post_voice(session_id,"slide_20","mila_gift","What did Mila bring you?")
        packed_items=["jacket","water","camera"]
        await post_interactive(session_id,"slide_24","suitcase",{"packed_items":packed_items,"selected":packed_items,"completed":True});await post_voice(session_id,"slide_24","take_trip","What will you take and why?")
        video_key="slide_20:media/mila-intro.mp4";await post_interactive(session_id,"slide_20","pre_slide_video",{"video_key":video_key,"outcome":"ended","completed":True})
        resumed=await client.post("/api/mobile/session/start",headers=headers,json={"child_id":child_id,"lesson_id":"demo_001"});assert resumed.status==200
        resumed_payload=await resumed.json();assert resumed_payload["session_id"]==session_id and resumed_payload["resumed"] is True
        resumed_state=resumed_payload["interactive_state"]["slide_24"];assert resumed_state["packed_items"]==packed_items and resumed_state["selected"]==packed_items
        assert resumed_payload["interactive_state"]["slide_20"]["selected_gift_id"]=="book"
        assert resumed_payload["pre_slide_video_state"]=={"attempt":[video_key],"ever":[video_key]}
        await post_voice(session_id,"slide_47","zebra","Tell me about the zebra.")
        for phrase,prompt in (("penguin","What can a penguin do?"),("parrot","What color is the parrot?")):
            await post_interactive(session_id,"slide_46","animal_compare",{"selected_animal_id":phrase});await post_voice(session_id,"slide_46",phrase,prompt)
        for phrase,prompt in (("lion","What can a lion do?"),("slide_51:turtle","What can a turtle do?")):
            await post_interactive(session_id,"slide_51","animal_compare",{"selected_animal_id":phrase});await post_voice(session_id,"slide_51",phrase,prompt)
        await post_interactive(session_id,"slide_45","animal_riddle",{"selected_animal_id":"giraffe"});await post_voice(session_id,"slide_45","giraffe","Tell me about the giraffe.")
        await post_voice(session_id,"slide_42","polar_bear","Tell me about the polar bear.");await post_voice(session_id,"slide_44","parrot","Tell me about the red parrot.")
        await post_interactive(session_id,"slide_49","mood_choice",{"selected_mood":"happy","completed":True})
        runtime=[];seen=set();cursor="slide_01"
        while cursor and cursor in by_id and cursor not in seen:seen.add(cursor);runtime.append(cursor);cursor=by_id[cursor].get("next_slide")
        for index,_slide_id in enumerate(runtime):
            progress=await client.post(f"/api/mobile/session/{session_id}/progress",headers=headers,json={"current_step":index});assert progress.status==200
        blocked=await client.post(f"/api/mobile/session/{session_id}/complete",headers=headers,json={});assert blocked.status==409;blocked_payload=await blocked.json();assert blocked_payload["code"]=="REQUIRED_MOVIE_RECORDINGS_MISSING" and blocked_payload["missing_phrase_ids"]==["invite"]
        await post_voice(session_id,"slide_16","invite","Come and visit me!")
        completed=await client.post(f"/api/mobile/session/{session_id}/complete",headers=headers,json={});assert completed.status==200;completion=await completed.json();assert completion["missing_voice_phrases"]==[] and completion["missing_exact_voice_phrases"]==[] and completion["movie_status"] in {"PROCESSING","READY"}
        pending=[task for task in mobile_api._movie_tasks if not task.done()]
        if pending:await asyncio.wait_for(asyncio.gather(*pending),timeout=10)
        status=await client.get(f"/api/mobile/session/{session_id}/movie",headers=headers);movie=await status.json();assert movie["status"]=="READY" and movie["url"]
        next_attempt=await client.post("/api/mobile/session/start",headers=headers,json={"child_id":child_id,"lesson_id":"demo_001"});next_payload=await next_attempt.json();assert next_attempt.status==200 and next_payload["session_id"]!=session_id
        assert next_payload["pre_slide_video_state"]=={"attempt":[],"ever":[video_key]}
    finally:await client.close()
    async with sessions() as db:
        assert await db.scalar(select(func.count(InteractiveResult.id)))>=10
        accepted=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session_id,VoiceAttempt.status=="ACCEPTED_CORRECT"))).all()
        assert set(required_movie_phrase_ids(lesson))<=({attempt.phrase_id for attempt in accepted})
        invite_slot=await db.scalar(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id==session_id,MovieVoiceSlot.required_voice_id=='invite'));assert invite_slot.status=='RECORDED'
        assert (await db.get(LessonSession,session_id)).status=="COMPLETED"
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
    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "mobile_auth_secret", "movie-failure-test-secret-that-is-long-enough")
    app = web.Application();mobile_api.register_mobile_routes(app);client = TestClient(TestServer(app));await client.start_server()
    try:
        response = await client.get(
            f"/api/mobile/session/{session.id}/movie",
            headers={"Authorization": f"Bearer {issue_session_token(parent.id)}"},
        )
        payload = await response.json()
        assert response.status == 200 and payload["status"] == "FAILED" and payload["can_retry"] is True
        assert payload["error"] == mobile_api.MOVIE_RETRY_MESSAGE
        assert "FFmpeg" not in payload["error"] and "Railway" not in payload["error"] and "exit" not in payload["error"]
    finally:
        await client.close()
    await engine.dispose()


@pytest.mark.skipif(os.getenv("DOME_RUN_MOVIE_E2E") != "1", reason="set DOME_RUN_MOVIE_E2E=1 for the real 100-second FFmpeg render")
def test_real_all_exact_child_voices_render_canonical_m1_to_mp4(tmp_path, monkeypatch):
    ffmpeg = os.getenv("DOME_FFMPEG_BIN") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.fail("DOME_FFMPEG_BIN/ffmpeg is required for the movie E2E test")
    monkeypatch.setattr(settings, "ffmpeg_bin", ffmpeg)
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    hero = tmp_path / "hero.png"
    image = Image.new("RGBA", (400, 520), (0, 0, 0, 0));draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, 150, 165), fill=(245, 105, 75, 255))
    draw.polygon([(20, 85), (2, 110), (45, 120)], fill=(245, 105, 75, 255))
    draw.rounded_rectangle((145, 145, 285, 455), radius=55, fill=(30, 120, 235, 255))
    draw.polygon([(260, 240), (392, 305), (270, 335)], fill=(30, 120, 235, 255))
    image.save(hero)
    metadata = {
        "characterBoundingBox": [0.0, 0.038, 0.98, 0.837],
        "facingDirection": "LEFT",
        "headCenterX": 0.2, "headCenterY": 0.18,
        "bodyCenterX": 0.55, "bodyCenterY": 0.58,
    }
    voices = {};lesson = load_lesson();contract = load_movie_contract("demo_001", lesson)
    for phrase in required_movie_phrase_ids(lesson):
        path = tmp_path / f"{phrase}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16_000);stream.writeframes(b"\x00\x00" * 16_000)
        voices[phrase] = path
    output = tmp_path / "happy-path.mp4"
    result = build_mobile_lesson_movie(MovieRenderInputs(base_video=contract.base_video, character=hero, audio_by_phrase=voices, timeline=contract.timeline, output=output, lesson_dir=contract.lesson_dir, target_language="en", approved_phrase_ids=contract.approved_phrase_ids, expected_base_sha256=contract.expected_base_sha256, require_all_phrase_audio=True, character_metadata=metadata))
    assert result == output and output.stat().st_size > 100_000
    width,height,duration=_probe_video(output)
    assert (width,height)==(1920,1080) and duration>=99.9
