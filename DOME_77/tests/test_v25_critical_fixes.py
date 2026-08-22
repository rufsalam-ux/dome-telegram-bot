from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_transcription_format_is_supported():
    text = (ROOT / 'app/services/speech_pipeline.py').read_text(encoding='utf-8')
    assert '"response_format": "json"' in text
    assert 'verbose_json' not in text


def test_suitcase_has_dedicated_wait_state_and_next_guard():
    states = (ROOT / 'app/bot/states.py').read_text(encoding='utf-8')
    handlers = (ROOT / 'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'waiting_webapp = State()' in states
    assert 'await state.set_state(LessonFlow.waiting_webapp)' in handlers
    assert 'data.get("suitcase_pending")' in handlers
    assert 'current_phrase_id="take_trip"' in handlers
    assert 'await state.set_state(LessonFlow.waiting_voice)' in handlers


def test_followup_blocks_progression():
    handlers = (ROOT / 'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'followup_pending=True' in handlers
    assert 'if data.get("followup_pending")' in handlers


def test_render_logs_and_low_resource_mode():
    builder = (ROOT / 'app/services/cartoon_builder.py').read_text(encoding='utf-8')
    assert '"-threads", "1"' in builder
    assert '"-preset", "ultrafast"' in builder
    assert 'log.error("FFmpeg failed' in builder
    assert 'filters.append("[0:a][voice]amix' in builder
