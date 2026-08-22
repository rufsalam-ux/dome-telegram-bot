from pathlib import Path
import json

def test_v68_files():
    assert Path("README_V68_RU.md").exists()
    assert Path("config/v68_architecture.json").exists()

def test_admin_editor_and_persistence_code():
    h=Path("app/bot/handlers.py").read_text("utf-8")
    c=Path("app/core/config.py").read_text("utf-8")
    assert "Command('slideconfig')" in h
    assert "Command('slideprompt')" in h
    assert "Command('hwconfig')" in h
    assert 'dome_provider' in h
    assert "DATA_DIR" in c
    assert "reading_child_share" in h

def test_homework_ai_and_runtime_types():
    imp=Path("app/services/lesson_importer.py").read_text("utf-8")
    h=Path("app/bot/handlers.py").read_text("utf-8")
    assert "analyze_homework_with_ai" in imp
    for x in ["photo_task","real_world_find","interactive_scene","sound_position","syllable_split","video_pause_question"]:
        assert x in h or x in imp
