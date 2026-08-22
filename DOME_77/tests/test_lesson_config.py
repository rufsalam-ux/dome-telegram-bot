import json
from pathlib import Path


def test_first_lesson_has_ten_phrases_and_timeline_segments():
    lesson = json.loads(Path("content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    assert len(lesson["required_phrases"]) == 10
    assert len(lesson["timeline"]) == 10
    assert lesson["max_voice_seconds"] == 5.0
    assert [s["phrase_id"] for s in lesson["timeline"]] == [
        p["phrase_id"] for p in lesson["required_phrases"]
    ]


def test_all_preset_characters_exist_and_are_not_gender_grouped():
    from app.services.preset_characters import list_preset_characters, preset_character_path

    characters = list_preset_characters()
    assert len(characters) >= 6
    assert all("gender" not in character for character in characters)
    assert all(preset_character_path(character["id"]).exists() for character in characters)
