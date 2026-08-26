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


def preset_character_geometry(character_id: str) -> dict:
    """Stable one-time geometry for the front-facing authored catalog assets."""

    get_preset_character(character_id)
    return {
        "characterBoundingBox": [0.0, 0.0, 1.0, 1.0],
        "headCenterX": 0.5,
        "headCenterY": 0.25,
        "headBoundingBox": [0.24, 0.05, 0.52, 0.38],
        "bodyCenterX": 0.5,
        "bodyCenterY": 0.62,
        "facingDirection": "FRONT",
        "confidence": 1.0,
        "source": "preset_catalog",
        "analysisVersion": "character-geometry-v1",
    }


def preset_collage_path() -> Path:
    path = settings.content_root / "preset-characters" / "all_characters.png"
    if not path.exists():
        raise FileNotFoundError(path)
    return path
