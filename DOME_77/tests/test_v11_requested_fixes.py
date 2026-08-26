import json
from pathlib import Path

from app.bot.handlers import syllabify_phrase
from app.bot.keyboards import _with_icon

ROOT = Path(__file__).parents[1]

def test_first_scene_uses_lyosha_ground_line_and_safe_width():
    timeline=json.loads((ROOT/'content/lessons/demo_001/timeline.json').read_text(encoding='utf-8'))
    assert timeline[0]['floor_y_norm'] == 0.82
    assert timeline[0]['height_norm'] >= 0.4
    assert timeline[0]['max_width_norm'] <= 0.42
    assert timeline[0]['placement_side'] == 'left'

def test_slide_20_21_overlay_does_not_cover_images():
    lesson=json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    for sid in ('slide_20','slide_21'):
        slide=next(x for x in lesson['slides'] if x['slide_id']==sid)
        assert not slide.get('overlay_text')

def test_choice_labels_have_visual_icon():
    assert _with_icon('кошка').startswith('🐱')
    assert _with_icon('компас').startswith('🧭')

def test_syllable_helper_keeps_meaning():
    out=syllabify_phrase('кошка')
    assert 'собак' not in out.lower()
