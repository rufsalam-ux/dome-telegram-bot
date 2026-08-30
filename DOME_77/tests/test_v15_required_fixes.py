import json
from pathlib import Path

from app.services.lesson_revision import next_runtime_step, normalize_lesson_step
from app.bot.keyboards import card_choice_keyboard

ROOT = Path(__file__).resolve().parents[1]


def lesson():
    return json.loads((ROOT / 'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))


def test_removed_slides_are_physically_absent():
    orders = [int(s['order']) for s in lesson()['slides']]
    assert not any(32 <= order <= 39 for order in orders)
    assert 24 in orders and 40 in orders


def test_runtime_route_jumps_from_24_to_40():
    slides = lesson()['slides']
    idx31 = next(i for i, s in enumerate(slides) if s['order'] == 24)
    idx40 = next(i for i, s in enumerate(slides) if s['order'] == 40)
    assert next_runtime_step(slides, idx31) == idx40


def test_slide9_is_blocking_and_not_skippable():
    slide9 = next(s for s in lesson()['slides'] if s['order'] == 9)
    assert slide9['type'] == 'card_selector'
    assert slide9['blocking_interaction'] is True
    assert slide9['allow_skip'] is False
    keyboard = card_choice_keyboard(slide9['card_options'])
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row if b.callback_data]
    assert 'lesson:skip' not in callbacks
    assert 'lesson:next' not in callbacks


def test_old_session_steps_migrate_but_new_runtime_steps_do_not_shift():
    assert normalize_lesson_step(39, 11) == 32
    assert normalize_lesson_step(31, 14) == 32


def test_first_scene_has_current_lyosha_safe_placement():
    timeline = json.loads((ROOT / 'content/lessons/demo_001/timeline.json').read_text(encoding='utf-8'))
    first = timeline[0] if isinstance(timeline, list) else timeline['scenes'][0]
    assert first['floor_y_norm'] == 0.82
    assert first['height_norm'] >= 0.4
    assert first['x_end_norm'] == 0.38
