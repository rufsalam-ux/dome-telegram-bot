from pathlib import Path
import json


def lesson():
    return json.loads(Path('content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))


def test_current_lesson_and_10_required_phrases():
    data = lesson()
    assert len(data['slides']) == 34
    assert len(data['required_phrases']) == 10
    configured = {x['phrase_id'] for x in data['required_phrases']}
    referenced = [x['required_phrase_id'] for x in data['slides'] if x.get('required_phrase_id')]
    assert len(referenced) == 11  # one authored phrase is intentionally reused
    assert set(referenced) == configured


def test_video_and_timeline_parameters():
    data = lesson()
    assert data['video_reference']['base']['width'] == 1920
    assert data['video_reference']['base']['height'] == 1080
    assert [x['visible_start'] for x in data['timeline']] == [21,39,54,64,70,75,80,85,90,95]
    assert data['animation_mode'] == 'WHOLE_CHARACTER_FALLBACK_IMPROVED'
