from __future__ import annotations
import json
from pathlib import Path

DEFAULTS = {
    # Provider-neutral local 2D motion vocabulary. These profiles are safe for
    # a whole PNG and become joint parameters when segmented parts exist.
    'idle': {'mirror': False, 'rotation': 0.006, 'walk_bob': 0.0, 'body_bob': 1.5, 'blink_period': 3.8, 'reusable': True},
    'blink': {'mirror': False, 'rotation': 0.004, 'walk_bob': 0.0, 'body_bob': 1.0, 'blink_period': 2.8, 'reusable': True},
    'talk': {'mirror': False, 'rotation': 0.014, 'walk_bob': 0.0, 'body_bob': 2.0, 'mouth_pulse': True, 'reusable': True},
    'listen': {'mirror': False, 'rotation': 0.008, 'walk_bob': 0.0, 'body_bob': 1.2, 'head_tilt': .018, 'reusable': True},
    'walk_left': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'left', 'limb_cycle': True, 'reusable': True},
    'walk_right': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'right', 'limb_cycle': True, 'reusable': True},
    'turn_left': {'mirror': True, 'rotation': 0.018, 'walk_bob': 0.0, 'direction': 'left', 'reusable': True},
    'turn_right': {'mirror': False, 'rotation': 0.018, 'walk_bob': 0.0, 'direction': 'right', 'reusable': True},
    'wave': {'mirror': False, 'rotation': 0.026, 'walk_bob': 0.0, 'gesture': 'wave', 'reusable': True},
    'point': {'mirror': False, 'rotation': 0.014, 'walk_bob': 0.0, 'gesture': 'point', 'reusable': True},
    'happy': {'mirror': False, 'rotation': 0.020, 'walk_bob': 0.0, 'body_bob': 2.4, 'reusable': True},
    'thinking': {'mirror': False, 'rotation': 0.012, 'walk_bob': 0.0, 'head_tilt': .025, 'reusable': True},
    'small_jump': {'mirror': False, 'rotation': 0.018, 'walk_bob': 0.0, 'jump_height': 18.0, 'reusable': True},
    'enter_left': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'right', 'limb_cycle': True, 'reusable': True},
    'enter_right': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'left', 'limb_cycle': True, 'reusable': True},
    'exit_left': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'left', 'limb_cycle': True, 'reusable': True},
    'exit_right': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0, 'direction': 'right', 'limb_cycle': True, 'reusable': True},
    'tail_idle': {'mirror': False, 'rotation': 0.006, 'walk_bob': 0.0, 'tail_sway': .02, 'reusable': True},
    'tail_sway': {'mirror': False, 'rotation': 0.008, 'walk_bob': 0.0, 'tail_sway': .045, 'reusable': True},
    'walk': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0, 'limb_cycle': True, 'reusable': True},
    'turn': {'mirror': False, 'rotation': 0.018, 'walk_bob': 0.0, 'gesture': 'turn', 'reusable': True},
    'dance': {'mirror': False, 'rotation': 0.028, 'walk_bob': 3.0, 'gesture': 'dance', 'reusable': True},
    'pick_up': {'mirror': False, 'rotation': 0.020, 'walk_bob': 0.0, 'gesture': 'bend_pick', 'reusable': True},
    'stand_front_talk': {'mirror': False, 'rotation': 0.012, 'walk_bob': 0.0, 'look_at':'camera', 'talk':True, 'lip_sync':True, 'reusable':True},
    'stand_front_listen': {'mirror': False, 'rotation': 0.006, 'walk_bob': 0.0, 'look_at':'camera', 'talk':False, 'idle':True, 'reusable':True},
    'walk_from_left': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0, 'direction':'right', 'talk':True, 'lip_sync':True, 'reusable':True},
    'walk_from_right': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0, 'direction':'left', 'talk':True, 'lip_sync':True, 'reusable':True},
    'walk_right_to_left_talk': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0},
    'walk_left_to_right_talk': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0},
    'wave': {'mirror': False, 'rotation': 0.02, 'walk_bob': 0.0},
    'happy_jump': {'mirror': False, 'rotation': 0.025, 'walk_bob': 0.0},
    'point': {'mirror': False, 'rotation': 0.016, 'walk_bob': 0.0, 'gesture':'point'},
    'turn_to_friend': {'mirror': False, 'rotation': 0.020, 'walk_bob': 0.0, 'gesture':'turn'},
    'pick_up_object': {'mirror': False, 'rotation': 0.022, 'walk_bob': 0.0, 'gesture':'bend_pick'},
    'talk_excited': {'mirror': False, 'rotation': 0.020, 'walk_bob': 1.5, 'gesture':'talk'},
}


def ensure_animation_library(root: Path) -> Path:
    """Create reusable animation descriptors. Generated clips may be added later without changing timelines."""
    root.mkdir(parents=True, exist_ok=True)
    manifest=root/'manifest.json'
    if not manifest.exists():
        manifest.write_text(json.dumps({'version':2,'animations':DEFAULTS,'generated_assets':{},'notes':'Reusable motion descriptors. AI-generated/rigged assets can be registered by the desktop Animation Builder.'},ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def animation_profile(name: str, root: Path) -> dict:
    ensure_animation_library(root)
    try:
        data=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        return dict(data.get('animations',{}).get(name) or DEFAULTS.get(name) or DEFAULTS['stand_front_talk'])
    except Exception:
        return dict(DEFAULTS.get(name) or DEFAULTS['stand_front_talk'])
