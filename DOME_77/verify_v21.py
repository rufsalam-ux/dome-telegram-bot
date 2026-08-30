from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
lesson = json.loads((ROOT/'content/lessons/demo_001/lesson.json').read_text(encoding='utf-8'))
slides = lesson['slides']
by_id = {s['slide_id']: s for s in slides}
assert by_id['slide_19']['character_box'], 'slide_19 character box missing'
assert by_id['slide_20']['character_box'], 'slide_20 character box missing'
assert by_id['slide_24']['interactive_task'] == 'suitcase'
assert by_id['slide_24']['allow_skip'] is True
assert by_id['slide_24']['voice_after_action_optional'] is True
assert all(not (25 <= int(s.get('order',0)) <= 39) for s in slides), 'slides 25-39 must be physically absent'
for name in ['background.png','compass.png','camera.png','phone.png','teddy.png','flower.png','telescope.png','fish.png','jacket.png']:
    assert (ROOT/'app/webapp/static/assets/suitcase'/name).exists(), name
html=(ROOT/'app/webapp/static/index.html').read_text(encoding='utf-8')
assert 'pointerdown' in html and 'sendData' in html and "type:'suitcase'" in html
renderer=(ROOT/'app/services/slide_renderer.py').read_text(encoding='utf-8')
assert 'No translations, white boxes, captions or other text' in renderer
handlers=(ROOT/'app/bot/handlers.py').read_text(encoding='utf-8')
assert 'биометрических персональных данных' in handlers
assert 'одноразовым кодом' in handlers
print('DOME v21 verification: OK')
