import json
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]

def test_free_topic_hidden_but_system_kept():
    cfg=json.loads((ROOT/'config/free_topic.json').read_text(encoding='utf-8'))
    assert cfg['enabled'] is True
    assert cfg['show_in_child_menu'] is False
    handlers=(ROOT/'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'menu:free_topic' in handlers
    assert 'show_in_child_menu' in handlers

def test_suitcase_assets_are_real_images():
    p=ROOT/'app/webapp/static/assets/suitcase'
    for name in ['jacket','hat','boots','camera','gloves','swimsuit','flippers','shorts']:
        f=p/f'{name}.png'
        assert f.exists() and f.stat().st_size>1000
        im=Image.open(f).convert('RGBA')
        assert im.getchannel('A').getbbox() is not None

def test_all_movie_lines_still_reachable():
    lesson=json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    timeline=json.loads((ROOT/'content/lessons/demo_001/timeline.json').read_text(encoding='utf-8'))
    by={s['slide_id']:s for s in lesson['slides']}
    cur='slide_01'; seen=[]
    while cur and cur not in seen:
        seen.append(cur); cur=by[cur].get('next_slide')
    route_required={by[x].get('required_phrase_id') for x in seen if by[x].get('required_phrase_id')}
    timeline_required={x['phrase_id'] for x in timeline}
    assert timeline_required <= route_required
    assert 'invite' in route_required

def test_video_architecture_has_safe_image_fallback():
    handlers=(ROOT/'app/bot/handlers.py').read_text(encoding='utf-8')
    assert 'media_type' in handlers and 'answer_video' in handlers
    assert '_send_course_slide_media' in handlers

def test_penguin_compare_preserves_movie_line():
    lesson=json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
    s=next(x for x in lesson['slides'] if x['slide_id']=='slide_46')
    assert s['type']=='animal_compare'
    assert s['required_phrase_id']=='penguin'
    assert s['preserve_required_movie_line'] is True
