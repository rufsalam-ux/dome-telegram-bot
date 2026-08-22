import json
from pathlib import Path
from app.services.animation_engine.motion_planner import normalize_motion_plan
from app.services.animation_engine.rig_loader import load_character_rig


def test_v49_new_actions_support_back_view_and_dance(tmp_path):
    plan = normalize_motion_plan({
        "actions": [
            {"action": "turn", "from_view": "front", "to_view": "back", "duration": .5},
            {"action": "walk", "view": "back", "duration": 2},
            {"action": "dance", "view": "front", "style": "happy", "duration": 3},
        ]
    })
    assert [x.action for x in plan.commands] == ["turn", "walk", "dance"]
    assert plan.commands[1].view == "back"


def test_v49_legacy_timeline_still_normalizes():
    plan = normalize_motion_plan({"animation":"walk_from_left","visible_start":1,"end":3,"talk_start":2})
    assert plan.commands[0].action == "walk"
    assert plan.lip_sync is True


def test_v49_fallback_rig_does_not_require_provider(tmp_path):
    png=tmp_path/'hero.png'; png.write_bytes(b'fake')
    rig=load_character_rig(png,tmp_path/'rigs')
    assert rig.provider == 'fallback_png'
    assert rig.views['front'].endswith('hero.png')


def test_animation_library_defines_back_and_dance():
    p=Path('content/animations/library.json')
    data=json.loads(p.read_text(encoding='utf-8'))
    assert 'back' in data['views']
    assert 'dance' in data['primitive_actions']
