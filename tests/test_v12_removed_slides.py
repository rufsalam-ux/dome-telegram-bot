import json
from pathlib import Path
from app.services.lesson_revision import normalize_v12_lesson_step

ROOT=Path(__file__).parents[1]

def load():
    return json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))

def test_slides_32_to_39_are_removed():
    d=load(); orders=[s['order'] for s in d['slides']]
    assert all(n not in orders for n in range(32,40))
    assert len(d['slides']) == 41

def test_slide_31_goes_directly_to_40():
    d=load(); slides={s['order']:s for s in d['slides']}
    assert slides[31]['next_slide'] == 'slide_40'
    assert orders_after_31(d)[:2] == [40,41]

def orders_after_31(d):
    orders=[s['order'] for s in d['slides']]
    i=orders.index(31)
    return orders[i+1:]

def test_saved_steps_are_migrated():
    assert normalize_v12_lesson_step(30) == 30
    assert normalize_v12_lesson_step(31) == 31
    assert normalize_v12_lesson_step(38) == 31
    assert normalize_v12_lesson_step(39) == 31
    assert normalize_v12_lesson_step(46) == 38
