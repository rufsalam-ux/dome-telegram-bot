from pathlib import Path
import json
from app.services.animation_engine.character_motion_library import CharacterMotionLibrary, signature
from app.services.animation_engine.kling_provider import enabled
from app.services.lesson_loader import load_lesson


def test_timeline_has_russian_animation_descriptions():
    lesson=load_lesson('demo_001')
    assert lesson['timeline']
    assert all((x.get('character_animation') or {}).get('description_ru') for x in lesson['timeline'])


def test_suitcase_new_assets_exist():
    root=Path('app/webapp/static/assets/suitcase')
    for name in ['jacket','binoculars','water','compass','teddy','camera','telescope','fish','notebook','sunglasses','background']:
        assert (root/f'{name}.png').exists()


def test_motion_signature_stable():
    a=signature('Герой идет вправо и говорит',speaking=True,view='front',duration=4.8)
    b=signature('Герой идет вправо и говорит',speaking=True,view='front',duration=5.0)
    assert a==b
