from pathlib import Path
import json


def lesson():
    return json.loads(Path('content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))


def test_49_slides_and_10_required_phrases():
    data = lesson()
    assert len(data['slides']) == 41
    assert len(data['required_phrases']) == 10
    assert sum(bool(x.get('required_phrase_id')) for x in data['slides']) == 10


def test_video_and_timeline_parameters():
    data = lesson()
    assert data['video_reference']['base']['width'] == 910
    assert data['video_reference']['base']['height'] == 512
    assert [x['visible_start'] for x in data['timeline']] == [21,39,55,64,69,74,79,85,90,95]
    assert data['animation_mode'] == 'WHOLE_CHARACTER_FALLBACK_IMPROVED'
