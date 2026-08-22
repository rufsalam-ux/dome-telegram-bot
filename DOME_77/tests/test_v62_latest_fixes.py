from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
H = (ROOT / "app/bot/handlers.py").read_text(encoding="utf-8")
LESSON = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))


def test_voice_without_phrase_is_recovered_before_db_write():
    assert 'storage_phrase_id = data.get("current_phrase_id")' in H
    assert 'if not storage_phrase_id:' in H
    assert 'voice_without_phrase_recovered' in H
    guard_pos = H.index('if not storage_phrase_id:')
    db_pos = H.index('db.add(VoiceAttempt(')
    assert guard_pos < db_pos


def test_invite_model_is_short_and_consistent():
    phrase = next(x for x in LESSON['required_phrases'] if x['phrase_id'] == 'invite')
    slide = next(x for x in LESSON['slides'] if x['slide_id'] == 'slide_16')
    assert phrase['simplified_text'] == 'Приезжайте ко мне!'
    assert slide['simplified_text'] == 'Приезжайте ко мне!'
    assert slide['max_voice_seconds'] == 5
    assert slide['allow_ai_followup'] is False
    assert slide['suppress_ai_followup'] is True
    assert 'Я хочу поехать на море' not in json.dumps(slide, ensure_ascii=False)


def test_invite_slide_emits_once_under_global_idempotency_guard():
    assert 'duplicate_slide_suppressed' in H
    assert 'last_emission_key' in H
    slide = next(x for x in LESSON['slides'] if x['slide_id'] == 'slide_16')
    assert slide['next_slide'] == 'slide_49'
