from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
H=(ROOT/'app/bot/handlers.py').read_text(encoding='utf-8')
AI=(ROOT/'app/services/ai_speech.py').read_text(encoding='utf-8')
VIS=(ROOT/'app/services/visual_localization.py').read_text(encoding='utf-8')
CART=(ROOT/'app/services/cartoon_builder.py').read_text(encoding='utf-8')
RUNTIME=(ROOT/'app/services/animation_engine/runtime_provider.py').read_text(encoding='utf-8')


def test_suitcase_gate_is_scoped_and_animals_never_get_suitcase_message():
    assert 'if slide_id == "slide_24" or data.get("suitcase_pending") or data.get("suitcase_completed")' in H
    assert 'if data.get("animal_compare_pending"):' in H
    assert '🐾 Сначала выбери животное' in H


def test_non_cartoon_animal_phrase_can_be_skipped():
    assert 'cartoon_phrase_ids=' in H
    assert 'allow_skip=not is_cartoon_phrase' in H
    assert 'a non-cartoon animal phrase may be skipped' in H


def test_duplicate_task_guard_present():
    assert 'duplicate_slide_suppressed' in H
    assert 'last_emission_key' in H


def test_lyosha_never_alex_policy():
    assert '__DOME_LYOSHA__' in AI
    assert '"Lyosha"' in AI
    assert 'never Alex' in VIS


def test_final_render_is_bounded_and_nonblocking():
    cfg=json.loads((ROOT/'config/cartoon.json').read_text(encoding='utf-8'))
    assert cfg['generate_missing_animation_during_render'] is False
    assert cfg['ffmpeg_timeout_seconds'] <= 180
    assert cfg['total_render_timeout_seconds'] <= 210
    assert 'asyncio.to_thread(build_timeline_cartoon' in H
    assert 'asyncio.wait_for' in H
    assert 'allow_generate=allow_generate_during_render' in CART
    assert 'allow_generate:bool=True' in RUNTIME


def test_new_clean_animal_assets_are_present():
    for name in ('parrot.png','lion.png','penguin.png','turtle.png'):
        p=ROOT/'app/webapp/static/assets/animals'/name
        assert p.exists() and p.stat().st_size>10000
    for name in ('animal-pair-penguin-parrot.png','animal-pair-lion-turtle.png'):
        p=ROOT/'content/lessons/demo_001/lesson-images'/name
        assert p.exists() and p.stat().st_size>10000
