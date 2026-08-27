from pathlib import Path

from app.core.config import settings
from app.services.character_geometry import ANALYSIS_VERSION, attach_character_rig


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
    return attach_character_rig({
        "characterBoundingBox": [0.0, 0.0, 1.0, 1.0],
        "sourceWidth": 1024,
        "sourceHeight": 1024,
        "visibleAspectRatio": 1.0,
        "headCenterX": 0.5,
        "headCenterY": 0.25,
        "headPoint": [0.5, 0.25],
        "headBoundingBox": [0.24, 0.05, 0.52, 0.38],
        "bodyCenterX": 0.5,
        "bodyCenterY": 0.62,
        "torsoBoundingBox": [0.23, 0.35, 0.54, 0.5],
        "frontSide": "FRONT",
        "backSide": "BACK",
        "frontPoint": [0.5, 0.28],
        "backPoint": [0.5, 0.62],
        "frontLimbs": [],
        "rearLimbs": [],
        "leftArmOrFrontLimb": [0.31, 0.55],
        "rightArmOrFrontLimb": [0.69, 0.55],
        "leftHandOrFrontPaw": [0.27, 0.69],
        "rightHandOrFrontPaw": [0.73, 0.69],
        "leftLegOrRearLimb": [0.39, 0.9],
        "rightLegOrRearLimb": [0.61, 0.9],
        "feetAnchor": [0.5, 0.98],
        "groundAnchor": [0.5, 0.98],
        "tailBoundingBox": None,
        "tailPoint": None,
        "facingDirection": "FRONT",
        "canonicalFacing": "FRONT",
        "confidence": 1.0,
        "source": "preset_catalog",
        "userConfirmed": True,
        "analysisVersion": ANALYSIS_VERSION,
    }, trusted=True)


def preset_collage_path() -> Path:
    path = settings.content_root / "preset-characters" / "all_characters.png"
    if not path.exists():
        raise FileNotFoundError(path)
    return path
