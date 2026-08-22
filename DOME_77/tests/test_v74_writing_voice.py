import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'app/webapp/static/free_topic_task.html').read_text(encoding='utf-8')
HANDLERS=(ROOT/'app/bot/handlers.py').read_text(encoding='utf-8')
IMPORTER=(ROOT/'app/services/lesson_importer.py').read_text(encoding='utf-8')
MAIN=(ROOT/'app/main.py').read_text(encoding='utf-8')


def lesson(name):
    return json.loads((ROOT/'content/lessons'/name/'lesson.json').read_text(encoding='utf-8'))


def test_v74_banner_and_version():
    assert (ROOT/'VERSION').read_text().strip()=='74'
    assert 'DOME v74 RUNTIME WRITING + VOICE READY' in MAIN


def test_ios_downward_gesture_is_locked_during_interaction():
    assert 'disableVerticalSwipes' in HTML
    assert 'interactionLock' in HTML
    assert "window.addEventListener('touchmove'" in HTML
    assert 'capture:true,passive:false' in HTML
    assert 'setInteractionLock(true)' in HTML
    assert 'overflow:hidden!important' in HTML


def test_trace_rejects_short_outside_and_scribble_and_speaks_feedback():
    for token in ['trace_max_outside','trace_max_scribble','regionCoverage','directionChanges','scribbleScore','traceFeedback']:
        assert token in HTML
    assert "speakLocal(text)" in HTML
    assert "Линий слишком много" in HTML
    assert "вести линию ближе к пунктиру" in HTML


def test_trace_policy_is_runtime_configurable():
    for token in ["trace_checkpoints","trace_min_coverage","trace_max_outside","trace_max_scribble"]:
        assert token in HANDLERS


def test_mastery_tasks_cannot_be_skipped_in_chitayka_and_book_stories():
    mastery={'drag_drop','trace','read_aloud','read_roles','match_visible','matching','tap_sound','voice_answer','dialogue','comprehension','retell','repeat','speak'}
    for d in [lesson('chitayka_001_auo'),lesson('book_stories_001')]:
        for s in d['slides']:
            if s.get('type') in mastery or s.get('expects_answer'):
                assert s.get('can_skip') is False, s.get('slide_id')


def test_future_imports_default_mastery_to_required():
    assert "mastery_required=typ in" in IMPORTER
    assert "'can_skip':not (expects_answer or mastery_required)" in IMPORTER
    assert "'can_skip':False,'mastery_required':True" in IMPORTER


def test_teacher_feedback_is_spoken_for_reading_and_speech():
    assert 'async def _send_teacher_voice' in HANDLERS
    assert 'role_retry_' in HANDLERS
    assert 'role_ok_' in HANDLERS
    assert 'speech_retry_' in HANDLERS
    assert 'speech_ok_' in HANDLERS


def test_old_session_results_are_rejected():
    assert 'expected_instance' in HANDLERS
    assert 'Это окно относится к другому уроку или старой сессии' in HANDLERS


def test_non_skippable_message_is_generic_not_only_cartoon():
    assert 'Это важное задание урока. Я помогу выполнить его, но пропускать его нельзя.' in HANDLERS
