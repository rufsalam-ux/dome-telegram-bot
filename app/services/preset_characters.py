from pathlib import Path

from app.core.config import settings


PRESET_CHARACTERS: list[dict] = [
    {"id": "robot", "title": "Робот", "file": "robot.png"},
    {"id": "fox", "title": "Лисёнок", "file": "fox.png"},
    {"id": "cat", "title": "Кот", "file": "cat.png"},
    {"id": "dragon", "title": "Дракончик", "file": "dragon.png"},
    {"id": "explorer", "title": "Путешественник", "file": "explorer.png"},
    {"id": "star", "title": "Звёздный герой", "file": "star.png"},
]


def list_preset_characters() -> list[dict]:
    return PRESET_CHARACTERS


def get_preset_character(character_id: str) -> dict:
    for character in PRESET_CHARACTERS:
        if character["id"] == character_id:
            return character
    raise KeyError(character_id)


def preset_character_path(character_id: str) -> Path:
    character = get_preset_character(character_id)
    path = settings.content_root / "preset-characters" / character["file"]
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def preset_collage_path() -> Path:
    path = settings.content_root / "preset-characters" / "all_characters.png"
    if not path.exists():
        raise FileNotFoundError(path)
    return path
