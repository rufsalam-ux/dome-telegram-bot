import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from tools.dome_admin import make_app
from app.services.homework import resolve_homework


def test_control_center_includes_builder_routes():
    app = make_app()
    resources = {str(r.resource) for r in app.router.routes()}
    assert '<PlainResource  /builder>' in resources
    assert '<PlainResource  /api/settings>' in resources
    assert '<PlainResource  /api/lessons>' in resources


def test_manual_homework_from_lesson_manifest():
    folder = Path('content/lessons/test_v35_hw')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'manifest.json'
    path.write_text(json.dumps({
        'lesson_id': 'test_v35_hw',
        'homework': {
            'enabled': True,
            'source': 'manual',
            'duration_minutes': 6,
            'instructions': 'Повтори слова.',
            'activities': [{'type': 'speak', 'instruction': 'Скажи две фразы.'}],
            'send_to_bot': True,
            'send_to_parent_email': True,
            'allow_skip': True,
            'allow_defer': True,
            'keep_in_archive': True,
        }
    }, ensure_ascii=False), 'utf-8')
    child = SimpleNamespace(target_language='en', language_level='A1', age_years=9)
    try:
        text, duration, cfg = asyncio.run(resolve_homework(child, [], 'test_v35_hw'))
        assert 'Повтори слова.' in text
        assert 'Скажи две фразы.' in text
        assert duration == 6
        assert cfg['send_to_bot'] is True
    finally:
        if path.exists(): path.unlink()
        if folder.exists(): folder.rmdir()
