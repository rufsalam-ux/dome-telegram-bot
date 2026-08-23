import json
from pathlib import Path
from app.engine.activity_registry import REGISTRY
from app.services.authored_content import SUPPORTED_CONTENT_TYPES

ROOT=Path(__file__).resolve().parents[1]

def test_v67_prices_and_access():
    p=json.loads((ROOT/'config/pricing.json').read_text('utf-8'))
    assert p['currency']=='EUR'
    assert [x['monthly_price'] for x in p['regular_course']['subscription_plans']]==[39,69,99,139]
    assert [x['annual_price'] for x in p['regular_course']['subscription_plans']]==[429,759,1089,1536]
    assert p['regular_course']['max_completed_runs']==2
    assert p['regular_course']['lesson_access_months']==10

def test_v67_test_billing_off():
    p=json.loads((ROOT/'config/payments.json').read_text('utf-8'))
    assert p['billing_enabled'] is False
    assert p['test_payment_mode'] is True

def test_book_stories_and_chitayka_homework_present():
    book=json.loads((ROOT/'content/lessons/book_stories_001/lesson.json').read_text('utf-8'))
    assert len(book['slides'])==97
    assert any(x['type']=='read_roles' for x in book['slides'])
    assert any(x['type']=='read_aloud' for x in book['slides'])
    hw=json.loads((ROOT/'content/lessons/chitayka_001_auo/homework.json').read_text('utf-8'))
    assert len(hw['slides'])==3
    assert all(x['type']=='trace' for x in hw['slides'])

def test_no_proof_activity_types():
    assert 'proof_transfer' not in REGISTRY
    assert 'find_ai_mistake' not in REGISTRY
    assert 'explain_to_ai' not in REGISTRY
    assert {'read_roles','tap_sound','connect_lines','sentence_builder'} <= SUPPORTED_CONTENT_TYPES
