from pathlib import Path
import json

from app.services.authored_content import validate_content_lesson
from app.services.lesson_importer import _slide_number_for_extra, infer_type


def base(slide):
    return {
        'engine':'content_v1','schema_version':'1.4','lesson_id':'x','course_id':'reading','title':'X','order':1,
        'max_completed_runs':2,'expires_after_months':10,'slides':[{'slide_id':'s1','order':1,**slide}],
    }


def test_choice_cannot_publish_without_correct_answer():
    errors=validate_content_lesson(base({'type':'choice','options':['a','b']}))
    assert any('needs correct' in x for x in errors)


def test_video_pause_requires_real_pause_question_and_correct_answer():
    broken=base({'type':'video_pause_question','video_url':'https://example.test/a.mp4','options':['a','b']})
    e=validate_content_lesson(broken)
    assert any('pause_at_seconds' in x for x in e)
    assert any('correct_indices' in x for x in e)
    good=base({'type':'video_pause_question','video_file':'source_materials/extras/slide_3.mp4','pause_at_seconds':4,'question':'Кто?','options':['кот','пёс'],'correct_indices':[0]})
    assert validate_content_lesson(good)==[]


def test_role_reading_must_be_ordered_and_have_both_speakers():
    bad=base({'type':'read_roles','reading_text':'текст','role_turns':[{'role':'Маша','speaker':'child','text':'Привет'}]})
    assert any(('ordered role_turns' in x or 'both child and bot' in x) for x in validate_content_lesson(bad))
    good=base({'type':'read_roles','reading_text':'Привет! Здравствуй!','role_turns':[{'role':'Маша','speaker':'child','text':'Привет!'},{'role':'Лиса','speaker':'bot','text':'Здравствуй!'}]})
    assert validate_content_lesson(good)==[]


def test_extra_video_slide_mapping_uses_caption_or_filename():
    assert _slide_number_for_extra(Path('movie.mp4'),'слайд 11 видео')==11
    assert _slide_number_for_extra(Path('slide_7.mp4'),'')==7
    assert infer_type('Видео: остановить на паузу и задать вопрос')=='video_pause_question'


def test_runtime_guards_are_wired_into_real_handlers():
    h=Path('app/bot/handlers.py').read_text('utf-8')
    for token in ['complete_session_once','_personal_release_enabled','_continue_role_reading','runtime_state_json','authored_homework_assignment_id','mark_cartoon_generated','correct_indices']:
        assert token in h
    assert 'mark_authored_completed' not in h


def test_payment_webhook_reserves_event_before_side_effects():
    s=Path('app/webapp/server.py').read_text('utf-8')
    lifecycle=Path('app/services/payment_lifecycle.py').read_text('utf-8')
    reserve=s.index("db.add(PaymentWebhookEvent")
    checkout=s.index("if typ=='checkout.session.completed'")
    assert reserve < checkout
    assert 'except IntegrityError' in s
    assert 'access_source=ev.provider.upper()' in lifecycle


def test_import_is_staged_and_conversation_cartoon_enabled():
    s=Path('app/services/lesson_importer.py').read_text('utf-8')
    assert '.import-' in s and 'before_reimport' in s
    assert "'make_cartoon':course_id=='conversation'" in s
    assert 'extra_file_notes' in s


def test_admin_runtime_controls_present():
    h=Path('app/bot/handlers.py').read_text('utf-8')
    for cmd in ['unpublishlesson','lessonorder','extrarun','previewlesson','validate_lesson','lessonrestore']:
        assert cmd in h


def test_dynamic_role_choice_is_valid_and_runtime_wired():
    good=base({
        'type':'read_roles','reading_text':'Привет! Здравствуй!','available_roles':['Маша','Лиса'],
        'role_turns':[{'role':'Маша','text':'Привет!','image_file':'images/1.jpg'},{'role':'Лиса','text':'Здравствуй!','image_file':'images/2.jpg'}],
    })
    assert validate_content_lesson(good)==[]
    h=Path('app/bot/handlers.py').read_text('utf-8')
    assert "rolepick:" in h and 'role_selected_role' in h and "turn.get('image_file')" in h


def test_bundled_book_story_passes_strict_publish_validation():
    root=Path('content/lessons/book_stories_001')
    lesson=json.loads((root/'lesson.json').read_text('utf-8'))
    from app.services.authored_content import validate_homework
    homework=json.loads((root/'homework.json').read_text('utf-8'))
    assert validate_content_lesson(lesson)==[]
    assert validate_homework(homework)==[]
    assert lesson['slides'][14]['video_url'].startswith('https://www.youtube.com/watch?v=')


def test_legacy_conversation_uses_same_two_run_entitlement_ledger():
    demo=json.loads(Path('content/lessons/demo_001/lesson.json').read_text('utf-8'))
    assert demo['max_completed_runs']==2
    assert demo['expires_after_months']==10
    assert demo['cartoon_on_first_run_only'] is False
    h=Path('app/bot/handlers.py').read_text('utf-8')
    resume=h[h.index('async def _resume_or_start_lesson'):h.index('@router.callback_query(F.data == "lesson:continue")')]
    assert 'can_start_authored' in resume and 'ensure_test_entitlement' in resume
    assert '>= 3' not in resume and 'пройден три раза' not in resume
    finish=h[h.index('async def send_step'):h.index('async def _resume_or_start_lesson')]
    assert 'completed_before > 0' in finish
    assert 'Новый мультфильм не создаётся' in finish
    assert 'complete_session_once' in finish and 'mark_cartoon_generated' in finish


def test_preview_validates_homework_and_extra_video_upload_supported():
    h=Path('app/bot/handlers.py').read_text('utf-8')
    preview=h[h.index("@router.message(Command('previewlesson'))"):h.index('VOWELS =',h.index("@router.message(Command('previewlesson'))"))]
    assert 'validate_homework' in preview
    assert '@router.message(AdminLessonImport.extras, F.video)' in h


def test_release_schedule_has_plan_change_baseline():
    models=Path('app/db/models.py').read_text('utf-8')
    rel=Path('app/services/subscription_release.py').read_text('utf-8')
    lifecycle=Path('app/services/payment_lifecycle.py').read_text('utf-8')
    changes=Path('app/services/subscription_plan_changes.py').read_text('utf-8')
    assert 'release_baseline_count' in models
    assert 'baseline + weeks_open * freq' in rel
    assert 'len(subscription_lesson_ids) - baseline' in rel
    assert 'activate_pending_after_successful_payment' in lifecycle
    assert 'PLAN_CHANGE_ACTIVATED' in changes


def test_chitayka_required_drag_drop_is_real_spatial_drag():
    lesson=json.loads(Path('content/lessons/chitayka_001_auo/lesson.json').read_text('utf-8'))
    by={int(s['order']):s for s in lesson['slides']}
    for n,letter in [(6,'А'),(14,'У')]:
        s=by[n]
        assert s['type']=='drag_drop'
        assert s['items'] and all(x==letter for x in s['items'])
        assert len(s['items'])==len(s['targets'])==len(s['drop_zones'])
    html=Path('app/webapp/static/free_topic_task.html').read_text('utf-8')
    assert 'draggable=true' in html and 'ondrop=' in html and 'dropZones' in html


def test_completion_preserves_runtime_for_safe_cartoon_retry():
    access=Path('app/services/lesson_access.py').read_text('utf-8')
    assert 'runtime_state_json="{}"' not in access
    h=Path('app/bot/handlers.py').read_text('utf-8')
    start=h[h.index('async def _start_authored_content_lesson'):h.index('def _authored_runtime_snapshot')]
    assert 'retry it from the persisted' in start
    assert 'free_topic_voice_files' in start
    finish=h[h.index('async def _finish_free_topic'):h.index('async def free_topic_repeat')]
    assert 'await _persist_authored_runtime(state)' in finish


def test_admin_can_resend_homework_report_and_retry_cartoon():
    h=Path('app/bot/handlers.py').read_text('utf-8')
    for cmd in ['resendhomework','resendreport','retrycartoon']:
        assert f'Command("{cmd}")' in h
    assert 'send_homework_email' in h
    assert 'send_progress_report' in h


def test_human_friendly_interaction_aliases_are_publishable_and_canonicalized():
    from app.services.authored_content import canonical_content_type
    assert canonical_content_type('true_false')=='choice'
    assert canonical_content_type('pronunciation')=='repeat'
    assert canonical_content_type('tap_to_hear')=='tap_sound'
    assert canonical_content_type('sequencing')=='sequence'
    assert canonical_content_type('mini_dictation')=='dictation'
    tf=base({'type':'true_false','options':['Да','Нет'],'correct_indices':[0]})
    assert validate_content_lesson(tf)==[]
