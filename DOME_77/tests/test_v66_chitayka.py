from pathlib import Path
import json

from app.services.authored_content import load_authored_lesson, validate_content_lesson, discover_course_lessons


def test_chitayka_real_lesson_is_discoverable():
    lesson = load_authored_lesson('chitayka_001_auo')
    assert lesson is not None
    assert lesson['course_id'] == 'learn_to_read'
    assert len(lesson['slides']) == 28
    assert validate_content_lesson(lesson) == []
    assert discover_course_lessons('learn_to_read')[0] == 'chitayka_001_auo'


def test_chitayka_required_interactions():
    lesson = load_authored_lesson('chitayka_001_auo')
    by_order = {int(s['order']): s for s in lesson['slides']}
    assert by_order[3]['video_url'].endswith('0Pkp9XmJO0M')
    assert by_order[11]['video_url'].endswith('ZFcLQyrm068')
    assert by_order[6]['type'] == 'drag_drop' and by_order[6]['items'] and all(x == 'А' for x in by_order[6]['items'])
    assert by_order[14]['type'] == 'drag_drop' and by_order[14]['items'] and all(x == 'У' for x in by_order[14]['items'])
    for n in [7,8,9,16,17,18,19,24]:
        assert by_order[n]['type'] == 'trace'
    assert by_order[25]['type'] == 'tap_sound'
    assert by_order[26]['type'] == 'match_visible'


def test_chitayka_assets_exist():
    root = Path('content/lessons/chitayka_001_auo')
    for n in range(1, 29):
        assert (root / 'images' / f'slide_{n:02d}.png').exists()
