import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_removed_slides_32_39_are_absent():
    lesson = json.loads((ROOT / 'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    orders = {int(s.get('order', 0)) for s in lesson['slides']}
    assert not orders.intersection(set(range(32, 40)))
    assert 31 in orders and 40 in orders


def test_slide9_is_blocking():
    lesson = json.loads((ROOT / 'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    slide = next(s for s in lesson['slides'] if s['slide_id'] == 'slide_09')
    assert slide['allow_skip'] is False
    assert slide['blocking_interaction'] is True


def test_required_cartoon_phrase_cannot_be_skipped():
    code = (ROOT / 'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'data.get("current_required_phrase")' in code
    assert 'slides[step].get("post_required_phrase_id")' in code


def test_first_scene_is_higher():
    lesson = json.loads((ROOT / 'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    scene = next(x for x in lesson['timeline'] if x['phrase_id'] == 'lesha_clothes')
    assert scene['y'] <= 150
    assert scene['height'] >= 280


def test_sms_consent_and_payment_docs_exist():
    assert (ROOT / 'app/services/sms_consent.py').exists()
    assert (ROOT / 'docs/PAYMENTS_AND_SMS_RU.md').exists()
    handlers = (ROOT / 'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'VOICE_RECORDING' in handlers
    assert 'PAYMENT' in handlers
    assert 'ConsentRecord' in handlers
