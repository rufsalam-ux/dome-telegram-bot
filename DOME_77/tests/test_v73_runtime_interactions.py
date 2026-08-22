import json
from pathlib import Path

HTML=Path('app/webapp/static/free_topic_task.html').read_text('utf-8')
HANDLERS=Path('app/bot/handlers.py').read_text('utf-8')
MAIN=Path('app/main.py').read_text('utf-8')


def lesson():
    return json.loads(Path('content/lessons/chitayka_001_auo/lesson.json').read_text('utf-8'))


def test_mobile_canvas_blocks_vertical_swipe_and_requires_trace_regions():
    assert 'disableVerticalSwipes' in HTML
    assert 'touch-action:none' in HTML
    assert "trace_regions" in HTML
    assert 'insideRatio>=0.78' in HTML
    assert 'covered===required.length' in HTML


def test_drag_drop_uses_authored_media_and_real_touch_drag():
    assert "image_url=authored_image_url if data.get('authored_mode')" in HANDLERS
    assert 'setPointerCapture' in HTML
    assert 'targetAt(' in HTML
    assert 'interchangeable' in HTML
    d=lesson(); by={int(s['order']):s for s in d['slides']}
    assert len(by[6]['items'])==len(by[6]['drop_zones'])==7
    assert len(by[14]['items'])==len(by[14]['drop_zones'])==6


def test_tap_sound_hotspots_corrected_and_invisible():
    d=lesson(); s=next(x for x in d['slides'] if int(x['order'])==25)
    assert len(s['hotspots'])==8
    assert s['min_taps']==4
    assert '.hotspot.hit{border-color:transparent;background:transparent}' in HTML


def test_visible_matching_preserves_pair_styles():
    d=lesson(); s=next(x for x in d['slides'] if int(x['order'])==26)
    assert [p['style'] for p in s['pairs']]==['teal','blue','green','gold']
    assert "style-'+style" in HTML
    assert "top.append" in HTML and "bot.append" in HTML


def test_old_miniapp_instance_cannot_complete_new_course():
    assert "'instance':instance" in HANDLERS or "'instance':str(data.get('authored_session_id')" in HANDLERS
    assert "early_payload.get('instance')" in HANDLERS
    assert 'другому уроку или старой сессии' in HANDLERS


def test_raw_openai_error_is_not_sent_to_child():
    assert 'AI voice error: {exc}' not in HANDLERS
    assert 'Голосовой помощник сейчас временно недоступен' in HANDLERS
    assert 'DOME v73 RUNTIME INTERACTIONS + VOICE FIX' in MAIN
