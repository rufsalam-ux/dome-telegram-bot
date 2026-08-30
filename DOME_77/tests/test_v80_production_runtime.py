import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonSession, MovieVoiceSlot, Parent, VoiceAttempt
from app.services.authored_content import normalized_media_sequence, validate_content_lesson
from app.services.conversational_tutor import adaptive_follow_up_policy
from app.services.lesson_loader import LessonConfigurationError, load_lesson
from app.services.lesson_runtime import correction_for_assessment
from app.services.mobile_lesson_movie import all_movie_phrase_ids, ensure_movie_voice_slots, record_movie_voice_slot, required_movie_phrase_ids, resolve_movie_voice_slots


ROOT=Path(__file__).resolve().parents[1]


def test_content_schema_template_and_video_to_image_sequence_are_publishable():
    schema=json.loads((ROOT/'content/schemas/lesson.schema.json').read_text('utf-8'))
    template=json.loads((ROOT/'content/templates/pilot_lesson_template/lesson.json').read_text('utf-8'))
    assert schema['$defs']['media']['properties']['type']['enum']==['image','video','animation','youtube','audio']
    assert validate_content_lesson(template)==[]
    media_slide=next(slide for slide in template['slides'] if slide['slide_id']=='step_01_media')
    assert [item['type'] for item in normalized_media_sequence(media_slide)]==['video','image']


def test_production_loader_falls_back_from_invalid_persistent_edit(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'storage_root',tmp_path/'storage')
    live=settings.storage_root/'authored-content/lessons/chitayka_001_auo';live.mkdir(parents=True)
    (live/'lesson.json').write_text('{broken json',encoding='utf-8')
    lesson=load_lesson('chitayka_001_auo')
    assert lesson['content_source']=='bundled' and lesson['publication_status']=='PUBLISHED'


def test_draft_is_previewable_but_never_served_as_production(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'storage_root',tmp_path/'storage')
    live=settings.storage_root/'authored-content/lessons/draft_001';live.mkdir(parents=True)
    draft={'engine':'content_v1','schema_version':'1.4','lesson_id':'draft_001','course_id':'conversation','title':'Draft','order':99,'status':'draft','active':False,'max_completed_runs':2,'expires_after_months':10,'slides':[{'slide_id':'s1','order':1,'type':'passive','prompt':'Preview'}]}
    (live/'lesson.json').write_text(json.dumps(draft),encoding='utf-8')
    assert load_lesson('draft_001',preview=True)['publication_status']=='DRAFT'
    with pytest.raises(LessonConfigurationError,match='not published'):load_lesson('draft_001')


def test_adaptive_followups_require_a_strong_independent_answer():
    strong=adaptive_follow_up_policy(authored_enabled=True,authored_max=2,language_level='A2',attempt_number=1,transcript='I would take my camera',confidence=.94,semantic_match=.92)
    weak=adaptive_follow_up_policy(authored_enabled=True,authored_max=2,language_level='A2',attempt_number=2,transcript='camera',confidence=.61,semantic_match=.55)
    pre_a1=adaptive_follow_up_policy(authored_enabled=True,authored_max=2,language_level='PRE_A1',attempt_number=1,transcript='red camera',confidence=.95,semantic_match=.95)
    assert strong[:2]==(True,2);assert weak[0] is False;assert pre_a1[:2]==(True,2)
    assert correction_for_assessment(accepted=False,semantic_match=.2,attempt_number=2,ai_correction='A long unrelated correction',authored_example='A camera.',goal='What will you take?')=='A camera.'


@pytest.mark.asyncio
async def test_movie_voice_slots_allow_only_exact_whitelisted_child_recordings(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'openai_api_key','');engine=create_async_engine('sqlite+aiosqlite:///:memory:');sessions=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
    lesson=load_lesson('demo_001');required=required_movie_phrase_ids(lesson);by_id={row['phrase_id']:row for row in lesson['required_phrases']}
    exact_audio=tmp_path/'exact.wav';exact_audio.write_bytes(b'exact-child-audio')
    compatible_audio=tmp_path/'compatible.wav';compatible_audio.write_bytes(b'compatible-child-audio')
    async with sessions() as db:
        parent=Parent(email='slots@example.com',email_verified=True);db.add(parent);await db.flush();child=Child(parent_id=parent.id,display_name='Slots');db.add(child);await db.flush();session=LessonSession(child_id=child.id,lesson_id='demo_001');db.add(session);await db.flush()
        exact=VoiceAttempt(lesson_session_id=session.id,phrase_id=required[0],attempt_number=1,audio_path=str(exact_audio),status='ACCEPTED_CORRECT',transcript=by_id[required[0]]['target_text']);compatible=VoiceAttempt(lesson_session_id=session.id,phrase_id='warmup_non_movie',attempt_number=1,audio_path=str(compatible_audio),status='ACCEPTED_CORRECT',transcript=by_id[required[1]]['target_text']);db.add_all([exact,compatible]);await db.flush()
        slots=await ensure_movie_voice_slots(db,session.id,lesson);assert len(slots)==len(all_movie_phrase_ids(lesson)) and all(slot.status=='EXPECTED' for slot in slots)
        assert await record_movie_voice_slot(db,session.id,required[0],exact,lesson) is True;await db.commit()
        immediate=await db.scalar(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id==session.id,MovieVoiceSlot.required_voice_id==required[0]));assert immediate.status=='RECORDED' and immediate.source_attempt_id==exact.id
        resolved,diagnostics=await resolve_movie_voice_slots(db,session.id,[exact,compatible],lesson,'ru',tmp_path/'cache');await db.commit()
        assert resolved=={required[0]:exact_audio}
        assert diagnostics[0]['strategy']=='exact_child_recording'
        assert all(item['strategy'] in {'missing_required_child_recording','optional_child_choice_skipped'} for item in diagnostics[1:])
        assert any(item['required_voice_id']=='take_trip' and item['status']=='OPTIONAL_SKIPPED' for item in diagnostics)
        assert all(item['required_voice_id']!=required[1] or item['status']=='MISSING_REQUIRED' for item in diagnostics)
    await engine.dispose()


def test_mobile_completion_blocks_only_until_required_child_movie_takes_exist():
    source=(ROOT/'app/webapp/mobile_api.py').read_text('utf-8');player=(ROOT.parent/'DOME_MOBILE_77/src/screens/LessonPlayer.tsx').read_text('utf-8')
    assert 'REQUIRED_MOVIE_RECORDINGS_MISSING' in source
    assert 'allow_tutor_tts' in (ROOT/'content/lessons/demo_001/movie_manifest.json').read_text('utf-8')
    assert 'Продолжить с примером' not in player
