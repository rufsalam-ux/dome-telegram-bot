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


PRESET_RIG_GEOMETRY: dict[str, dict] = {
    "robot": {
        "characterBoundingBox": [.065, .149, .868, .826], "headPoint": [.50, .25],
        "headBoundingBox": [.30, .15, .40, .22], "torsoBoundingBox": [.25, .33, .50, .40],
        "eyeBoundingBoxes": [[.39, .21, .075, .07], [.535, .21, .075, .07]], "mouthBoundingBox": [.39, .275, .22, .055],
        "frontLimbs": [[.065, .43, .20, .19], [.735, .43, .20, .19]],
        "rearLimbs": [[.28, .72, .17, .25], [.55, .72, .17, .25]],
        "leftArmOrFrontLimb": [.25, .45], "rightArmOrFrontLimb": [.75, .45],
        "leftHandOrFrontPaw": [.11, .58], "rightHandOrFrontPaw": [.89, .58],
        "leftLegOrRearLimb": [.34, .91], "rightLegOrRearLimb": [.66, .91], "groundAnchor": [.50, .975],
    },
    "fox": {
        "characterBoundingBox": [.198, .168, .602, .807], "headPoint": [.50, .38],
        "headBoundingBox": [.20, .17, .60, .56], "torsoBoundingBox": [.27, .35, .46, .37],
        "eyeBoundingBoxes": [[.36, .44, .075, .075], [.565, .44, .075, .075]], "mouthBoundingBox": None,
        "frontLimbs": [], "rearLimbs": [[.29, .70, .15, .27], [.56, .70, .15, .27]],
        "leftLegOrRearLimb": [.36, .92], "rightLegOrRearLimb": [.64, .92], "groundAnchor": [.50, .975],
    },
    "cat": {
        "characterBoundingBox": [.198, .157, .602, .818], "headPoint": [.50, .48],
        "headBoundingBox": [.26, .31, .48, .37], "torsoBoundingBox": [.34, .65, .32, .24],
        "eyeBoundingBoxes": [[.38, .43, .075, .08], [.55, .43, .075, .08]], "mouthBoundingBox": [.41, .55, .18, .08],
        "frontLimbs": [], "rearLimbs": [[.33, .84, .14, .14], [.55, .84, .14, .14]],
        "leftLegOrRearLimb": [.39, .93], "rightLegOrRearLimb": [.62, .93], "groundAnchor": [.50, .975],
    },
    "dragon": {
        "characterBoundingBox": [.065, .101, .868, .874], "headPoint": [.50, .31],
        "headBoundingBox": [.33, .18, .34, .28], "torsoBoundingBox": [.25, .40, .50, .40],
        "eyeBoundingBoxes": [[.425, .29, .055, .07], [.55, .29, .055, .07]], "mouthBoundingBox": [.42, .38, .16, .055],
        "frontLimbs": [[.065, .37, .20, .27], [.735, .37, .20, .27]],
        "rearLimbs": [[.29, .75, .14, .22], [.57, .75, .14, .22]],
        "leftArmOrFrontLimb": [.27, .48], "rightArmOrFrontLimb": [.73, .48],
        "leftHandOrFrontPaw": [.12, .55], "rightHandOrFrontPaw": [.88, .55],
        "leftLegOrRearLimb": [.35, .93], "rightLegOrRearLimb": [.65, .93], "groundAnchor": [.50, .975],
    },
    "explorer": {
        "characterBoundingBox": [.157, .111, .685, .864], "headPoint": [.50, .25],
        "headBoundingBox": [.34, .11, .32, .25], "torsoBoundingBox": [.29, .36, .42, .40],
        "eyeBoundingBoxes": [[.405, .215, .06, .07], [.535, .215, .06, .07]], "mouthBoundingBox": [.42, .285, .16, .06],
        "frontLimbs": [[.15, .39, .18, .29], [.67, .39, .18, .29]],
        "rearLimbs": [[.31, .72, .16, .25], [.53, .72, .16, .25]],
        "leftArmOrFrontLimb": [.31, .42], "rightArmOrFrontLimb": [.69, .42],
        "leftHandOrFrontPaw": [.20, .62], "rightHandOrFrontPaw": [.80, .62],
        "leftLegOrRearLimb": [.38, .92], "rightLegOrRearLimb": [.62, .92], "groundAnchor": [.50, .975],
    },
    "star": {
        "characterBoundingBox": [.148, .259, .702, .716], "headPoint": [.50, .47],
        "headBoundingBox": [.20, .27, .60, .42], "torsoBoundingBox": [.30, .38, .40, .31],
        "eyeBoundingBoxes": [[.39, .45, .065, .07], [.55, .45, .065, .07]], "mouthBoundingBox": [.40, .54, .20, .07],
        "frontLimbs": [[.15, .57, .20, .21], [.65, .57, .20, .21]],
        "rearLimbs": [[.34, .68, .13, .29], [.54, .68, .13, .29]],
        "leftArmOrFrontLimb": [.32, .61], "rightArmOrFrontLimb": [.68, .61],
        "leftHandOrFrontPaw": [.18, .74], "rightHandOrFrontPaw": [.82, .74],
        "leftLegOrRearLimb": [.40, .91], "rightLegOrRearLimb": [.60, .91], "groundAnchor": [.50, .975],
    },
}


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
    path = preset_character_path(character_id)
    from PIL import Image
    with Image.open(path) as image:
        source_width, source_height = image.size
    authored = PRESET_RIG_GEOMETRY[character_id]
    ground = authored.get("groundAnchor") or [.5, .98]
    return attach_character_rig({
        **authored,
        "sourceWidth": source_width,
        "sourceHeight": source_height,
        "visibleAspectRatio": 1.0,
        "headCenterX": authored["headPoint"][0],
        "headCenterY": authored["headPoint"][1],
        "bodyCenterX": 0.5,
        "bodyCenterY": 0.62,
        "frontSide": "FRONT",
        "backSide": "BACK",
        "frontPoint": [0.5, 0.28],
        "backPoint": [0.5, 0.62],
        "feetAnchor": ground,
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
