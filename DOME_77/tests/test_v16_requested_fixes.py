import json
from pathlib import Path

from app.services.lesson_revision import normalize_lesson_step, next_runtime_step

ROOT = Path(__file__).resolve().parents[1]
LESSON = json.loads((ROOT / 'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
SLIDES = LESSON['slides']


def test_removed_slides_are_physically_absent():
    orders = [s['order'] for s in SLIDES]
    assert 2 not in orders
    assert not (set(range(32, 40)) & set(orders))
    assert len(SLIDES) == 34
    images = ROOT / 'content/lessons/demo_001/lesson-images'
    assert not (images / 'slide-02.png').exists()
    for n in range(32, 40):
        assert not (images / f'slide-{n:02d}.png').exists()


def test_direct_transitions():
    by_order = {s['order']: s for s in SLIDES}
    assert by_order[1]['next_slide'] == 'slide_03'
    assert by_order[24]['next_slide'] == 'slide_40'
    idx31 = next(i for i,s in enumerate(SLIDES) if s['order'] == 24)
    assert SLIDES[next_runtime_step(SLIDES, idx31)]['order'] == 40


def test_suitcase_action_and_movie_dialogue_are_both_required():
    by_order = {s['order']: s for s in SLIDES}
    assert by_order[19]['allow_skip'] is False
    assert by_order[19]['skip_prelude_only'] is False
    assert by_order[24]['allow_skip'] is False
    assert by_order[24]['unskippable'] is True
    assert by_order[24]['requires_interactive_completion'] is True
    assert by_order[24]['voice_after_action_optional'] is False
    assert by_order[24]['requiredForMovie'] is True
    assert by_order[24]['required_phrase_id'] == 'take_trip'


def test_resume_mapping_from_v15():
    assert normalize_lesson_step(0, 15) == 0
    assert normalize_lesson_step(1, 15) == 1
    assert normalize_lesson_step(2, 15) == 1
    assert normalize_lesson_step(31, 15) == 32
    assert normalize_lesson_step(40, 15) == 32
