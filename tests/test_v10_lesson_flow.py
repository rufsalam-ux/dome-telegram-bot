import json
from pathlib import Path

LESSON=Path('content/lessons/demo_001/lesson.json')

def load(): return json.loads(LESSON.read_text(encoding='utf-8'))

def test_special_slide_flows():
    d=load(); slides={x['order']:x for x in d['slides']}
    assert slides[9]['type']=='card_selector'
    assert slides[16]['type']=='image_choice'
    assert slides[24]['interactive_task']=='suitcase'
    assert slides[41]['answer_mode']=='none'
    assert slides[49]['type']=='mood_choice'

def test_only_cartoon_lines_are_five_seconds():
    d=load()
    for s in d['slides']:
        if s.get('required_phrase_id') and not s.get('prelude_before_required'):
            assert s.get('max_voice_seconds',5) <= 5
        else:
            assert s.get('max_voice_seconds',60) >= 60

def test_hidden_sequence_slides_are_skipped():
    d=load(); slides={x['order']:x for x in d['slides']}
    assert all(slides[n].get('skip_in_runtime') for n in range(10,16))
    assert all(slides[n].get('skip_in_runtime') for n in range(25,32))

def test_animation_first_scene_position_and_mirror_library():
    d=load(); first=d['timeline'][0]
    assert first['y'] <= 210
    assert d['animation_library'].endswith('manifest.json')
