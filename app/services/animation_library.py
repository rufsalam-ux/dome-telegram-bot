from __future__ import annotations
import json
from pathlib import Path

DEFAULTS = {
    'stand_front_talk': {'mirror': False, 'rotation': 0.012, 'walk_bob': 0.0},
    'stand_front_listen': {'mirror': False, 'rotation': 0.006, 'walk_bob': 0.0},
    'walk_from_left': {'mirror': False, 'rotation': 0.016, 'walk_bob': 4.0},
    'walk_from_right': {'mirror': True, 'rotation': 0.016, 'walk_bob': 4.0},
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
        manifest.write_text(json.dumps({'version':1,'animations':DEFAULTS},ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


def animation_profile(name: str, root: Path) -> dict:
    ensure_animation_library(root)
    try:
        data=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        return dict(data.get('animations',{}).get(name) or DEFAULTS.get(name) or DEFAULTS['stand_front_talk'])
    except Exception:
        return dict(DEFAULTS.get(name) or DEFAULTS['stand_front_talk'])
