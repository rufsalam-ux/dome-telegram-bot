import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, InteractiveResult, LessonSession, Parent, VoiceAttempt
from app.services.audio_processing import VoiceActivity
from app.services.lesson_loader import load_lesson
from app.services.lesson_runtime import apply_adaptive_assessment, no_speech_feedback, voice_attempt_outcome
from app.services.mobile_tokens import issue_session_token
from app.services.speech_pipeline import SpeechAssessment, is_non_speech_transcript
from app.services import visual_localization
from app.webapp import mobile_api


ROOT = Path(__file__).resolve().parents[1]
MOBILE_ROOT = ROOT.parent / "DOME_MOBILE_77"


def test_no_speech_never_becomes_correct_even_when_retries_are_exhausted():
    first = voice_attempt_outcome("NO_SPEECH", 1, 3)
    third = voice_attempt_outcome("NO_SPEECH", 3, 3)
    assert first.needs_retry and not first.accepted and not first.advance_allowed
    assert third.status == "NO_SPEECH_CONTINUE"
    assert third.advance_allowed and not third.accepted and not third.needs_retry
    assert "не засчитана" in no_speech_feedback(3, 3)[0]
    assert all(is_non_speech_transcript(value) for value in ("", "[music]", "тишина", "аааа"))
    assert not is_non_speech_transcript("yes")


def test_live_adaptation_changes_difficulty_after_current_answer():
    child = SimpleNamespace(
        working_difficulty=0.15, language_level="PRE_A1", answers_count=0,
        comprehension_score=0.0, grammar_score=0.0, vocabulary_score=0.0,
        pronunciation_score=0.0, fluency_score=0.0, independence_score=0.0,
    )
    attempt = SimpleNamespace(attempt_number=1)
    assessment = SpeechAssessment(
        transcript="I am happy today", status="ACCEPTED_CORRECT", semantic_match=1.0,
        grammar_errors=[], pronunciation_errors=[],
    )
    difficulty, level = apply_adaptive_assessment(child, attempt, assessment)
    assert difficulty > 0.15 and child.answers_count == 1 and level == "PRE_A1"
    assert attempt.recommended_difficulty > 0


def test_demo_definition_contains_original_card_questions_and_declarative_mila_hero():
    lesson = load_lesson("demo_001")
    slides = {item["slide_id"]: item for item in lesson["slides"]}
    questions = slides["slide_09"]["card_question_sets"]
    assert list(questions) == ["A", "Б", "В", "Г", "Д", "Е"]
    assert all(len(items) == 3 for items in questions.values())
    assert questions["A"][0]["text"] == "На завтрак я люблю..."
    assert questions["Е"][2]["text"] == "А куда бы ты хотел отправиться?"
    assert slides["slide_20"]["hero_placement"] == "left_of_mila"
    assert slides["slide_20"]["hero_box"] == [0.04, 0.35, 0.24, 0.61]
    assert slides["slide_20"]["interaction_kind"] == "gift_selector"
    assert [item["id"] for item in slides["slide_20"]["selection_options"]] == ["teddy", "book", "flowers", "backpack"]
    assert lesson["default_hero_placement"] == "hidden"


@pytest.mark.asyncio
async def test_localized_visual_is_immutable_cached_and_reused(monkeypatch, tmp_path):
    source = tmp_path / "slide-01.png"; original = b"russian-source" * 200; source.write_bytes(original)
    calls = 0

    async def fake_provider(_source, language):
        nonlocal calls; calls += 1
        assert language == "en"
        return b"english-localized" * 200

    monkeypatch.setattr(visual_localization, "_request_localized_image", fake_provider)
    first = await visual_localization.localize_embedded_text_image(source, tmp_path / "cache", "en", asset_version="78")
    second = await visual_localization.localize_embedded_text_image(source, tmp_path / "cache", "en", asset_version="78")
    assert first == second and first != source and calls == 1
    assert source.read_bytes() == original
    assert b"english-localized" in first.read_bytes()


@pytest.mark.asyncio
async def test_english_visual_verification_rejects_any_remaining_cyrillic():
    class Response:
        status_code = 200
        def json(self):
            return {"choices":[{"message":{"content":json.dumps({"has_cyrillic":True,"text_matches_target_language":False})}}]}
    class Client:
        async def post(self, *_args, **_kwargs):return Response()
    with pytest.raises(visual_localization.VisualLocalizationError, match="no-Cyrillic"):
        await visual_localization._verify_localized_image(b"generated-image", "en", Client())


@pytest.mark.asyncio
async def test_mobile_silence_gate_three_attempts_and_resume_selection(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        parent = Parent(email="runtime@example.com", email_verified=True);db.add(parent);await db.flush()
        child = Child(parent_id=parent.id, display_name="Runtime", target_language="en", native_language="ru");db.add(child);await db.flush()
        lesson_session = LessonSession(child_id=child.id, lesson_id="demo_001", status="IN_PROGRESS");db.add(lesson_session);await db.flush()
        db.add(InteractiveResult(lesson_session_id=lesson_session.id, slide_id="slide_09", task_type="card_selector", result_json=json.dumps({"selected_card_id":"A","card_question_index":1,"completed":False}), score=1.0))
        await db.commit();parent_id, child_id, session_id = parent.id, child.id, lesson_session.id

    def fake_prepare(_raw, wav, _max):
        wav.write_bytes(b"RIFF" + b"0" * 2000);return wav

    def fake_activity(_wav):
        return VoiceActivity(2.0, 0.0, 0.0, -80.0, -70.0, False, "INSUFFICIENT_SPEECH")

    async def forbidden_assessment(*_args, **_kwargs):
        raise AssertionError("ASR must not run for silence")

    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "prepare_child_voice", fake_prepare)
    monkeypatch.setattr(mobile_api, "analyze_voice_activity", fake_activity)
    monkeypatch.setattr(mobile_api, "assess_speech", forbidden_assessment)
    monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(settings, "mobile_auth_secret", "runtime-test-secret-that-is-definitely-long-enough")
    token = issue_session_token(parent_id);headers = {"Authorization": f"Bearer {token}"}
    app = web.Application();mobile_api.register_mobile_routes(app);client = TestClient(TestServer(app));await client.start_server()
    payload = {"audio_base64":base64.b64encode(b"0" * 1200).decode(),"slide_id":"slide_01","phrase_id":"slide_01","prompt":"How do you feel?"}
    try:
        responses=[]
        for _ in range(3):
            response=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=payload)
            assert response.status==200;responses.append(await response.json())
        assert responses[0]["needs_retry"] is True and responses[0]["accepted"] is False
        assert responses[2]["status"]=="NO_SPEECH_CONTINUE"
        assert responses[2]["advance_allowed"] is True and responses[2]["accepted"] is False
        assert "засчитана" in responses[2]["feedback"] and "отлич" not in responses[2]["feedback"].lower()
        async with sessions() as db:
            state,phrases=await mobile_api._mobile_resume_state(db,session_id)
            assert state["slide_09"]["selected_card_id"]=="A" and state["slide_09"]["card_question_index"]==1
            assert phrases==[]
    finally:
        await client.close();await engine.dispose()


@pytest.mark.asyncio
async def test_authenticated_english_visual_endpoint_never_returns_russian_source(monkeypatch, tmp_path):
    engine=create_async_engine("sqlite+aiosqlite:///:memory:");sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        parent=Parent(email="visual@example.com",email_verified=True);db.add(parent);await db.flush();child=Child(parent_id=parent.id,display_name="Visual",target_language="en",native_language="ru");db.add(child);await db.commit();parent_id,child_id=parent.id,child.id
    localized=tmp_path/"english.png";localized.write_bytes(b"english-only"*200)
    observed={}
    async def fake_localize(source,_root,language,**kwargs):observed.update(source=source,language=language,kwargs=kwargs);return localized
    monkeypatch.setattr(mobile_api,"SessionLocal",sessions);monkeypatch.setattr(mobile_api,"localize_embedded_text_image",fake_localize);monkeypatch.setattr(settings,"mobile_auth_secret","visual-test-secret-that-is-definitely-long-enough")
    token=issue_session_token(parent_id);app=web.Application();mobile_api.register_mobile_routes(app);client=TestClient(TestServer(app));await client.start_server()
    try:
        response=await client.get(f"/api/mobile/lesson/demo_001/visual/slide-01.png?child_id={child_id}&version=78",headers={"Authorization":f"Bearer {token}"})
        assert response.status==200 and await response.read()==localized.read_bytes()
        assert observed["language"]=="en" and observed["source"].name=="slide-01.png" and observed["kwargs"]["strict"] is True
    finally:
        await client.close();await engine.dispose()


def test_mobile_uses_localized_asset_pipeline_without_white_masks():
    player=(MOBILE_ROOT/"src/screens/LessonPlayer.tsx").read_text("utf-8")
    api=(MOBILE_ROOT/"src/api/mobile.ts").read_text("utf-8")
    assert "lessonVisualSource" in player and "lessonVisualSource" in api
    assert "LOCALIZED_IMAGE_MASKS" not in player
    assert "runtime-stage-${stage}" in player and "useSafeAreaInsets" in player and "useWindowDimensions" in player
