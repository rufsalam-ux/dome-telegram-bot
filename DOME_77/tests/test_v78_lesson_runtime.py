import base64
from io import BytesIO
import json
import math
import os
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, InteractiveResult, LessonSession, MovieVoiceSlot, Parent, VoiceAttempt
from app.services.audio_processing import VoiceActivity, analyze_voice_activity
from app.services.conversational_tutor import build_assessed_turn, no_speech_turn
from app.services.lesson_loader import load_lesson
from app.services.lesson_runtime import apply_adaptive_assessment, no_speech_feedback, voice_attempt_outcome
from app.services.adaptive_learning import proficiency_band
from app.services.mobile_tokens import issue_session_token
from app.services.speech_pipeline import SpeechAssessment, _transcription_confidence, is_non_speech_transcript
from app.services import ai_speech, visual_localization
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
    assert all(is_non_speech_transcript(value) for value in ("", "[music]", "тишина", "аааа", "um", "мм"))
    assert not is_non_speech_transcript("yes")


@pytest.mark.parametrize(("size","status"),[(0,400),(1,None),(999,None),(1000,None),(mobile_api.VOICE_MAX_UPLOAD_BYTES,None),(mobile_api.VOICE_MAX_UPLOAD_BYTES+1,413)])
def test_mobile_voice_upload_size_boundaries(size,status):
    response=mobile_api._voice_upload_size_error(size)
    assert (response.status if response is not None else None)==status


def test_asr_confidence_uses_provider_evidence_and_fails_closed_without_it():
    high = _transcription_confidence({"logprobs":[{"token":"Yes","logprob":-0.05},{"token":".","logprob":-0.1}]})
    quiet_whisper = _transcription_confidence({"segments":[{"start":0,"end":2,"avg_logprob":-0.3,"no_speech_prob":0.9}]})
    assert high > 0.9
    assert quiet_whisper < 0.1
    assert _transcription_confidence({"text":"hallucinated without evidence"}) == 0.0


def _write_pcm(path: Path, amplitude: int) -> None:
    rate = 16_000
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate)
        samples = [int(amplitude * math.sin(2 * math.pi * 220 * index / rate)) for index in range(rate * 2)]
        stream.writeframes(b"".join(struct.pack("<h", value) for value in samples))


def test_real_silent_and_near_silent_wav_are_rejected_before_asr(tmp_path):
    silent = tmp_path / "silent.wav";near = tmp_path / "near-silent.wav";voiced = tmp_path / "voiced.wav"
    _write_pcm(silent, 0);_write_pcm(near, 20);_write_pcm(voiced, 6_000)
    assert not analyze_voice_activity(silent).has_speech
    assert analyze_voice_activity(silent).reason == "INSUFFICIENT_SPEECH"
    assert not analyze_voice_activity(near).has_speech
    assert analyze_voice_activity(near).reason in {"INSUFFICIENT_SPEECH", "TOO_QUIET"}
    assert analyze_voice_activity(voiced).has_speech


def test_conversational_turn_is_bounded_and_progressive():
    turn = build_assessed_turn(
        {"reaction_target":"You chose the red kite — that sounds exciting!", "follow_up_target":"Where would you fly it? And who comes with you?", "emotion":"surprised"},
        accepted=True, allow_follow_up=True, follow_up_count=0, max_follow_ups=1,
    )
    assert turn.follow_up_target.count("?") == 1 and not turn.complete
    assert build_assessed_turn({"follow_up_target":"Another?"},accepted=True,allow_follow_up=True,follow_up_count=1,max_follow_ups=1).follow_up_target == ""
    assert no_speech_turn(1,3,target_retry="I didn't hear you.",native_hint="Попробуй ещё раз.").reason == "no_speech_retry"
    third = no_speech_turn(3,3,target_retry="Let's continue.")
    assert third.skipped and third.complete and third.reason == "no_speech_skipped"
    correction = build_assessed_turn(
        {"reaction_target":"Almost — a penguin cannot fly.","corrected_target":"A parrot can fly.","model_answer_target":"A parrot can fly.","native_hint":"Попугай умеет летать.","emotion":"gentle_correction"},
        accepted=False,allow_follow_up=True,follow_up_count=0,max_follow_ups=1,
    )
    assert correction.reason == "retry" and not correction.complete
    assert correction.follow_up_target == "" and correction.model_answer_target == "A parrot can fly."
    assert correction.emotion == "gentle_correction"
    grounded = build_assessed_turn(
        {"reaction_target":"Great!"}, accepted=True, allow_follow_up=False,
        follow_up_count=0, max_follow_ups=0, answer_text="I chose the red kite",
    )
    assert grounded.reaction_target == "I chose the red kite!"
    rejected = build_assessed_turn(
        {"reaction_target":"Nice!"}, accepted=False, allow_follow_up=False,
        follow_up_count=0, max_follow_ups=0, answer_text="unrelated",
    )
    assert rejected.reaction_target == ""


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


def test_adaptation_uses_latency_hints_native_language_and_open_question_signals():
    def child():
        return SimpleNamespace(working_difficulty=0.15,language_level="PRE_A1",answers_count=0,comprehension_score=0.0,grammar_score=0.0,vocabulary_score=0.0,pronunciation_score=0.0,fluency_score=0.0,independence_score=0.0)
    assessment=SpeechAssessment(transcript="I like the red parrot",status="ACCEPTED_CORRECT",semantic_match=.92,grammar_errors=[],pronunciation_errors=[])
    independent=child();supported=child();first=SimpleNamespace(attempt_number=1);second=SimpleNamespace(attempt_number=2)
    fast,_=apply_adaptive_assessment(independent,first,assessment,{"response_latency_ms":1800,"hints_used":0,"open_question":True})
    slow,_=apply_adaptive_assessment(supported,second,assessment,{"response_latency_ms":18_000,"hints_used":2,"used_native_language":True,"open_question":True})
    assert first.independence_score>second.independence_score
    assert fast>slow and proficiency_band(fast) in {"beginner","emerging","intermediate","strong"}


@pytest.mark.asyncio
async def test_bilingual_tts_synthesizes_each_text_in_its_own_language(monkeypatch, tmp_path):
    observed=[]
    async def fake_synthesize(text,language,_cache,purpose,style="warm"):
        observed.append((text,language,purpose,style));path=tmp_path/f"{purpose}.ogg";path.write_bytes(b"audio");return path
    def fake_run(command,**_kwargs):
        Path(command[-1]).write_bytes(b"joined-audio");return SimpleNamespace(returncode=0,stderr=b"")
    monkeypatch.setattr(ai_speech,"synthesize_speech",fake_synthesize);monkeypatch.setattr(ai_speech.subprocess,"run",fake_run)
    output=await ai_speech.synthesize_bilingual_speech("What did Mila bring?","en","Что Мила принесла?","ru",tmp_path/"cache","turn","curious")
    assert output and output.read_bytes()==b"joined-audio"
    assert observed==[
        ("What did Mila bring?","en","turn_target","curious"),
        ("Что Мила принесла?","ru","turn_native","encouraging"),
    ]


def test_tts_storage_reclaim_deletes_only_old_reconstructible_cache(monkeypatch, tmp_path):
    cache=tmp_path/"tts-cache-mobile";cache.mkdir()
    old=cache/"old.ogg";recent=cache/"recent.ogg";old.write_bytes(b"old");recent.write_bytes(b"recent")
    os.utime(old,(1.0,1.0));os.utime(recent,(19_999.0,19_999.0))
    monkeypatch.setattr(ai_speech.time,"time",lambda:20_000.0)

    def disk_usage(_path):
        return SimpleNamespace(free=8_000_000 if old.exists() else 32_000_000)

    monkeypatch.setattr(ai_speech.shutil,"disk_usage",disk_usage)
    stats=ai_speech.reclaim_tts_cache(cache,16_000_000)
    assert stats["files"]==1 and not old.exists()
    assert recent.exists()


def test_tts_output_uses_bounded_ephemeral_fallback_when_persistent_volume_is_full(monkeypatch, tmp_path):
    persistent=tmp_path/"persistent";fallback=tmp_path/"ephemeral"
    monkeypatch.setattr(ai_speech,"_ephemeral_tts_dir",lambda _path:fallback)
    monkeypatch.setattr(ai_speech.shutil,"disk_usage",lambda path:SimpleNamespace(free=0 if persistent in Path(path).parents or Path(path)==persistent else 64_000_000))
    output=ai_speech._tts_output_path(persistent,"voice.ogg",2_000_000)
    assert output==fallback/"voice.ogg"


def test_demo_definition_contains_original_card_questions_and_declarative_mila_hero():
    lesson = load_lesson("demo_001")
    slides = {item["slide_id"]: item for item in lesson["slides"]}
    questions = slides["slide_09"]["card_question_sets"]
    assert list(questions) == ["A", "Б", "В", "Г", "Д", "Е"]
    assert all(len(items) == 3 for items in questions.values())
    assert questions["A"][0]["text"] == "На завтрак я люблю..."
    assert questions["Е"][2]["text"] == "А куда бы ты хотел отправиться?"
    assert slides["slide_20"]["hero_placement"] == "left_of_mila"
    assert slides["slide_20"]["hero_box"] == lesson["hero_layout"]["anchors"]["left_of_mila"]
    assert slides["slide_20"]["interaction_kind"] == "gift_selector"
    assert [item["id"] for item in slides["slide_20"]["selection_options"]] == ["teddy", "book", "flowers", "backpack"]
    assert lesson["default_hero_placement"] == "right"
    assert lesson["default_hero_placement"] in lesson["hero_layout"]["anchors"]
    expected_states = ["ENTER","AI_SPEAKING","WAITING_ACTION","WAITING_VOICE","PROCESSING","FEEDBACK","FOLLOW_UP","RETRY","COMPLETE"]
    assert all(slide["runtime_state_machine"] == expected_states for slide in lesson["slides"])


@pytest.mark.asyncio
async def test_localized_visual_is_immutable_cached_and_reused(monkeypatch, tmp_path):
    source = tmp_path / "slide-01.png"
    image = Image.effect_noise((320, 180), 36);stream = BytesIO();image.save(stream, format="PNG");original = stream.getvalue();source.write_bytes(original)
    calls = 0

    async def fake_plan(_source, language):
        assert language == "en"
        return {"has_visible_text":True,"has_cyrillic":True,"text_regions":[{"source_text":"Привет","translated_text":"Hello","bbox_norm":[0.1,0.1,0.3,0.1]}]}

    async def fake_provider(_source, language, plan):
        nonlocal calls; calls += 1
        assert language == "en"
        assert plan["text_regions"][0]["translated_text"] == "Hello"
        localized = Image.effect_noise((320, 180), 24);output = BytesIO();localized.save(output, format="PNG");return output.getvalue()

    monkeypatch.setattr(visual_localization, "_request_localization_plan", fake_plan)
    monkeypatch.setattr(visual_localization, "_request_localized_image", fake_provider)
    first = await visual_localization.localize_embedded_text_image(source, tmp_path / "cache", "en", asset_version="78")
    second = await visual_localization.localize_embedded_text_image(source, tmp_path / "cache", "en", asset_version="78")
    assert first == second and first != source and calls == 1
    assert source.read_bytes() == original
    with Image.open(first) as localized_image:localized_image.verify()
    manifest = json.loads(first.with_suffix(".json").read_text("utf-8"))
    assert manifest["pipeline"] == ["ocr_text_region_detection", "translation", "background_restoration_inpainting", "translated_text_render", "language_verification", "immutable_cache"]
    assert manifest["target_language"] == "en"


@pytest.mark.asyncio
async def test_corrupt_or_wrong_manifest_visual_cache_is_regenerated_once(monkeypatch, tmp_path):
    source = tmp_path / "slide-01.png";image = Image.effect_noise((320,180),30);source_stream=BytesIO();image.save(source_stream,format="PNG");source.write_bytes(source_stream.getvalue())
    calls=0
    async def fake_plan(_source,_language):return {"has_visible_text":True,"has_cyrillic":True,"text_regions":[{"source_text":"Привет","translated_text":"Hello","bbox_norm":[0.1,0.1,0.3,0.1]}]}
    async def fake_provider(_source,_language,_plan):
        nonlocal calls;calls+=1;localized=Image.effect_noise((320,180),20);stream=BytesIO();localized.save(stream,format="PNG");return stream.getvalue()
    monkeypatch.setattr(visual_localization,"_request_localization_plan",fake_plan);monkeypatch.setattr(visual_localization,"_request_localized_image",fake_provider)
    key=visual_localization._cache_key(source,"en","79");output=tmp_path/"cache"/"localized-visuals"/"en"/f"slide-01_{key}.png";output.parent.mkdir(parents=True);output.write_bytes(b"not-a-png"*200)
    output.with_suffix(".json").write_text(json.dumps({"cache_key":"wrong"}),encoding="utf-8")
    first=await visual_localization.localize_embedded_text_image(source,tmp_path/"cache","en",asset_version="79")
    second=await visual_localization.localize_embedded_text_image(source,tmp_path/"cache","en",asset_version="79")
    assert first==second and calls==1
    with Image.open(first) as localized_image:localized_image.verify()


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

    translations=[]
    async def fake_translate(text,source,target):
        translations.append((text,source,target));return f"{target}:{text}"

    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "prepare_child_voice", fake_prepare)
    monkeypatch.setattr(mobile_api, "analyze_voice_activity", fake_activity)
    monkeypatch.setattr(mobile_api, "assess_speech", forbidden_assessment)
    monkeypatch.setattr(mobile_api, "translate_text", fake_translate)
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
        assert responses[0]["tutor_turn"]["reason"] == "no_speech_retry"
        assert responses[1]["tutor_turn"]["reason"] == "no_speech_model"
        assert responses[2]["tutor_turn"]["skipped"] is True
        assert responses[0]["tutor_turn"]["native_hint"] == "ru:How do you feel?"
        assert ("How do you feel?","en","ru") in translations
        assert "засчитана" in responses[2]["feedback"] and "отлич" not in responses[2]["feedback"].lower()
        required_payload={"audio_base64":base64.b64encode(b"0"*1200).decode(),"slide_id":"slide_19","phrase_id":"lesha_clothes","prompt":"Why are you dressed so warmly?"}
        required_responses=[]
        for _ in range(3):
            response=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=required_payload)
            assert response.status==200;required_responses.append(await response.json())
        assert all(item["status"]=="NO_SPEECH" and item["advance_allowed"] is False and item["movie_take_accepted"] is False for item in required_responses)

        def speech_activity(_wav):return VoiceActivity(2.0,1.3,.65,-24.0,-8.0,True,"SPEECH_DETECTED")
        async def rejected_assessment(*_args,**_kwargs):return SpeechAssessment(transcript="real child words",detected_language="en",confidence=.9,semantic_match=.1,status="REJECTED_MEANING")
        monkeypatch.setattr(mobile_api,"analyze_voice_activity",speech_activity);monkeypatch.setattr(mobile_api,"assess_speech",rejected_assessment)
        supported=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=required_payload);assert supported.status==200;supported_payload=await supported.json()
        assert supported_payload["status"]=="MOVIE_USABLE_WITH_SUPPORT" and supported_payload["advance_allowed"] is True and supported_payload["accepted"] is False and supported_payload["movie_take_accepted"] is True
        async with sessions() as db:
            state,phrases=await mobile_api._mobile_resume_state(db,session_id)
            assert state["slide_09"]["selected_card_id"]=="A" and state["slide_09"]["card_question_index"]==1
            assert phrases==["lesha_clothes"]
            slot=await db.scalar(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id==session_id,MovieVoiceSlot.required_voice_id=="lesha_clothes"));assert slot.status=="RECORDED"
    finally:
        await client.close();await engine.dispose()


async def _voice_upload_test_db(email: str):
    engine=create_async_engine("sqlite+aiosqlite:///:memory:");sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        parent=Parent(email=email,email_verified=True);db.add(parent);await db.flush()
        child=Child(parent_id=parent.id,display_name="Voice QA",target_language="en",native_language="ru");db.add(child);await db.flush()
        lesson_session=LessonSession(child_id=child.id,lesson_id="demo_001",status="IN_PROGRESS");db.add(lesson_session);await db.commit()
        return engine,sessions,parent.id,child.id,lesson_session.id


@pytest.mark.asyncio
async def test_mobile_voice_retry_is_idempotent_and_keeps_one_durable_compressed_take(monkeypatch,tmp_path):
    engine,sessions,parent_id,child_id,session_id=await _voice_upload_test_db("voice-idempotent@example.com")
    def fake_activity(_raw):return VoiceActivity(2.0,1.4,.7,-24.0,-7.0,True,"SPEECH_DETECTED")
    def fake_prepare(_raw,wav,_max):wav.write_bytes(b"RIFF"+b"prepared"*250);return wav
    async def fake_assess(*_args,**_kwargs):return SpeechAssessment(transcript="Why are you warm?",detected_language="en",confidence=.98,semantic_match=.98,status="ACCEPTED_CORRECT",grammar_errors=[],pronunciation_errors=[])
    async def fake_translate(text,_source,_target):return text
    monkeypatch.setattr(mobile_api,"SessionLocal",sessions);monkeypatch.setattr(mobile_api,"analyze_voice_activity",fake_activity);monkeypatch.setattr(mobile_api,"prepare_child_voice",fake_prepare);monkeypatch.setattr(mobile_api,"assess_speech",fake_assess);monkeypatch.setattr(mobile_api,"translate_text",fake_translate)
    monkeypatch.setattr(settings,"storage_root",tmp_path/"storage");monkeypatch.setattr(settings,"mobile_auth_secret","voice-idempotent-secret-that-is-long-enough")
    token=issue_session_token(parent_id);headers={"Authorization":f"Bearer {token}","Idempotency-Key":"v1-session-qa-same-file"};payload={"audio_base64":base64.b64encode(b"compressed-m4a"*200).decode(),"slide_id":"slide_19","phrase_id":"lesha_clothes","prompt":"Why are you dressed so warmly?"}
    app=web.Application();mobile_api.register_mobile_routes(app);client=TestClient(TestServer(app));await client.start_server()
    try:
        first=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=payload);second=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=payload)
        assert first.status==200 and second.status==200
        first_payload=await first.json();second_payload=await second.json();assert first_payload["idempotent_replay"] is False and second_payload["idempotent_replay"] is True
        async with sessions() as db:
            attempts=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session_id))).all();assert len(attempts)==1
            attempt=attempts[0];durable=Path(attempt.audio_path);assert attempt.client_recording_id=="v1-session-qa-same-file" and attempt.response_json
            assert attempt.audio_size_bytes==len(b"compressed-m4a"*200) and attempt.audio_mime_type=="audio/mp4"
            assert durable.suffix==".m4a" and durable.is_file() and durable.stat().st_size==attempt.audio_size_bytes
            slot=await db.scalar(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id==session_id,MovieVoiceSlot.required_voice_id=="lesha_clothes"));assert slot and slot.source_attempt_id==attempt.id
    finally:await client.close();await engine.dispose()


@pytest.mark.asyncio
async def test_mobile_voice_full_storage_fails_before_assessment_without_db_or_child_file(monkeypatch,tmp_path):
    engine,sessions,parent_id,child_id,session_id=await _voice_upload_test_db("voice-full@example.com")
    async def forbidden_assessment(*_args,**_kwargs):raise AssertionError("assessment must not run when durable storage is full")
    monkeypatch.setattr(mobile_api,"SessionLocal",sessions);monkeypatch.setattr(mobile_api,"assess_speech",forbidden_assessment);monkeypatch.setattr(mobile_api,"_voice_storage_capacity",lambda required:{"ready":False,"before":0,"after":0,"minimum":required+4*1024*1024})
    monkeypatch.setattr(settings,"storage_root",tmp_path/"storage");monkeypatch.setattr(settings,"mobile_auth_secret","voice-full-secret-that-is-definitely-long-enough")
    token=issue_session_token(parent_id);headers={"Authorization":f"Bearer {token}","Idempotency-Key":"v1-full-storage"};payload={"audio_base64":base64.b64encode(b"compressed-m4a"*200).decode(),"slide_id":"slide_19","phrase_id":"lesha_clothes","prompt":"Why are you dressed so warmly?"}
    app=web.Application();mobile_api.register_mobile_routes(app);client=TestClient(TestServer(app));await client.start_server()
    try:
        response=await client.post(f"/api/mobile/session/{session_id}/voice",headers=headers,json=payload);body=await response.json();assert response.status==507 and body["code"]=="VOICE_STORAGE_FULL" and body["retryable"] is True
        async with sessions() as db:assert not (await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session_id))).all()
        child_root=tmp_path/"storage"/"children"/str(child_id)/"mobile-voice"/str(session_id);assert not child_root.exists() or not list(child_root.glob("*"))
    finally:await client.close();await engine.dispose()


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
    assert "const initialHint=slide.always_bilingual?" in player
    root_app=(MOBILE_ROOT/"src/screens/RootApp.tsx").read_text("utf-8")
    movie_runtime=(MOBILE_ROOT/"src/engine/movieRuntime.ts").read_text("utf-8")
    movie_player=(MOBILE_ROOT/"src/components/MoviePlayer.tsx").read_text("utf-8")
    assert "MoviePlayer" in root_app and "Поделиться" in root_app and "useVideoPlayer" in movie_player
    assert "'QUEUED','RUNNING','PROCESSING'" in movie_runtime
