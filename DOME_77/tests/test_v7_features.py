import json
from pathlib import Path
from app.services.adaptive_learning import score_answer, level_from_score

ROOT=Path(__file__).resolve().parents[1]

def test_full_first_lesson_and_diagnostic():
    lesson=json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    assert len(lesson['slides'])==34
    assert lesson['slides'][0]['answer_mode']=='required_voice'
    assert lesson['slides'][0]['diagnostic'] is True
    assert all('overlay_text' not in s or isinstance(s['overlay_text'], str) for s in lesson['slides'])

def test_suitcase_interactive_config():
    lesson=json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    suitcase=next(s for s in lesson['slides'] if s.get('slide_id')=='slide_24')
    assert suitcase['interactive_task']=='suitcase'
    assert (ROOT/'app/webapp/static/index.html').exists()
    assert (ROOT/'app/webapp/static/fanfare.wav').exists()

def test_adaptive_scoring_and_level_stability():
    s=score_answer(semantic_match=.9,grammar_errors=[],pronunciation_errors=[],transcript='I am very happy today',attempt_number=1,status='ACCEPTED_CORRECT')
    assert s.recommended_difficulty>.65
    assert level_from_score(.9,'PRE_A1',2)=='PRE_A1'
    assert level_from_score(.9,'A1',10)=='B2'

def test_timeline_has_required_entries():
    t=json.loads((ROOT/'content/lessons/demo_001/timeline.json').read_text(encoding='utf-8'))
    assert len(t)==10
    assert t[0]['visible_start']==21.0
    assert t[-1]['visible_start']==95.0
