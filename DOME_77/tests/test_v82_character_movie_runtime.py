import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.db.models import Character
from app.services.cartoon_builder import (
    _resolve_normalized_timeline,
    _scheduled_voice_duration,
    _should_hflip,
    _visible_character_asset,
)
from app.services.cartoon_text_overlay import cartoon_text_filters, overlay_safe_zone_violations
from app.services.character_geometry import (
    ANALYSIS_VERSION,
    analyze_character_geometry,
    geometry_from_json,
    geometry_status,
)
from app.services.lesson_runtime import VOICE_FEEDBACK_STATES, classify_voice_feedback


ROOT = Path(__file__).resolve().parents[1]
LESSON_DIR = ROOT / "content/lessons/demo_001"


def _head_left_dinosaur(path: Path) -> None:
    image = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Head and snout are deliberately left of the lower-body centroid; the
    # long tail stays on the right so the test catches tail-based mirroring.
    draw.ellipse((20, 20, 145, 145), fill=(70, 170, 95, 255))
    draw.polygon([(20, 78), (2, 102), (42, 112)], fill=(70, 170, 95, 255))
    draw.rounded_rectangle((142, 132, 285, 350), radius=48, fill=(55, 145, 82, 255))
    draw.polygon([(260, 205), (392, 260), (270, 285)], fill=(55, 145, 82, 255))
    draw.rectangle((162, 330, 205, 390), fill=(55, 145, 82, 255))
    draw.rectangle((235, 330, 278, 390), fill=(55, 145, 82, 255))
    image.save(path)


@pytest.mark.asyncio
async def test_head_left_dinosaur_geometry_is_analyzed_once_and_persistable(tmp_path):
    source = tmp_path / "dinosaur.png"
    _head_left_dinosaur(source)
    geometry = await analyze_character_geometry(source, allow_remote=False)
    payload = geometry.payload()
    assert geometry.facingDirection == "LEFT"
    assert geometry.headCenterX < geometry.bodyCenterX
    assert geometry.confidence >= 0.7
    assert geometry.analysisVersion == ANALYSIS_VERSION
    assert geometry_status(geometry) == "READY"
    assert geometry_from_json(json.dumps(payload)) == payload
    assert set(Character.__table__.columns.keys()) >= {
        "visual_metadata_json", "visual_analysis_version", "visual_analysis_status"
    }


def test_visible_bbox_drives_scale_baseline_and_source_orientation(tmp_path):
    source = tmp_path / "dinosaur.png"
    cropped = tmp_path / "visible.png"
    _head_left_dinosaur(source)
    metadata = {"characterBoundingBox": [0.0, 0.05, 0.98, 0.93], "facingDirection": "LEFT"}
    result, aspect = _visible_character_asset(source, metadata, cropped)
    assert result == cropped and cropped.exists()
    assert Image.open(cropped).size == (392, 372)
    assert aspect == pytest.approx(392 / 372)

    face_right = {"visible_start": 0, "talk_start": 1, "end": 5, "animation": "walk_from_left"}
    face_left = {"visible_start": 0, "talk_start": 1, "end": 5, "animation": "walk_from_right"}
    front = {"visible_start": 0, "talk_start": 0, "end": 5, "animation": "stand_front_talk"}
    assert _should_hflip(face_right, "LEFT", False) is True
    assert _should_hflip(face_left, "LEFT", True) is False
    assert _should_hflip(front, "LEFT", True) is False

    authored = [{
        "visible_start": 0, "talk_start": 0, "end": 5,
        "height_norm": 0.4, "max_width_norm": 0.2, "floor_y_norm": 0.9,
        "x_norm": 0.5, "placement_side": "left",
        "protected_boxes_norm": [[0.45, 0.65, 0.25, 0.3]],
    }]
    placed = _resolve_normalized_timeline(authored, 1000, 1000, character_aspect=aspect)[0]
    assert placed["height"] <= 200
    assert placed["y"] + placed["height"] == 900
    assert placed["x"] + round(placed["height"] * aspect) < 450


def test_voice_feedback_states_are_mutually_exclusive_and_never_accept_silence():
    cases = {
        "NO_AUDIO": dict(audio_received=False, has_speech=False, transcript="", confidence=0, status="NO_SPEECH"),
        "NO_SPEECH": dict(audio_received=True, has_speech=False, transcript="", confidence=0, status="NO_SPEECH"),
        "ASR_FAILED": dict(audio_received=True, has_speech=True, transcript="", confidence=0, status="TECHNICAL_UNCERTAINTY"),
        "ANSWER_UNCLEAR": dict(audio_received=True, has_speech=True, transcript="hello", confidence=.2, status="TECHNICAL_UNCERTAINTY"),
        "INCORRECT": dict(audio_received=True, has_speech=True, transcript="banana", confidence=.9, status="RETRY"),
        "PARTIALLY_CORRECT": dict(audio_received=True, has_speech=True, transcript="red", confidence=.9, status="ACCEPTED_WITH_SUPPORT", semantic_match=.7),
        "CORRECT": dict(audio_received=True, has_speech=True, transcript="The parrot is red", confidence=.95, status="ACCEPTED_CORRECT", semantic_match=.95),
    }
    assert set(cases) == VOICE_FEEDBACK_STATES
    for expected, arguments in cases.items():
        assert classify_voice_feedback(**arguments) == expected
    assert classify_voice_feedback(**cases["NO_SPEECH"]) != "CORRECT"


def test_long_child_voice_extends_movie_schedule_instead_of_being_cut(monkeypatch, tmp_path):
    voice = tmp_path / "long.wav"
    voice.write_bytes(b"voice")
    monkeypatch.setattr("app.services.cartoon_builder._audio_duration", lambda _path: 8.0)
    segments = [{"visible_start": 28.0, "talk_start": 28.0, "end": 33.0, "audio_path": voice}]
    assert _scheduled_voice_duration(segments, 30.0) == pytest.approx(36.0)

    monkeypatch.setattr("app.services.cartoon_builder._audio_duration", lambda _path: 6.0)
    # A moderate 1.2x acceleration is allowed and fits the complete phrase.
    assert _scheduled_voice_duration(segments, 30.0) == pytest.approx(33.0)


def test_movie_captions_never_cover_bilingvadom_branding():
    document = json.loads((LESSON_DIR / "cartoon_text_overlays.json").read_text("utf-8"))
    branding = next(zone for zone in document["protected_zones"] if zone["id"] == "bilingvadom-branding")
    handle = next(zone for zone in document["protected_zones"] if zone["id"] == "bilingvadom-handle")
    assert branding == {"id": "bilingvadom-branding", "x": 1460, "y": 872, "w": 455, "h": 205}
    assert handle == {"id": "bilingvadom-handle", "x": 0, "y": 20, "w": 440, "h": 150, "windows": [[39.0, 44.0], [95.0, 100.0]]}
    assert overlay_safe_zone_violations(LESSON_DIR) == []
    filters = cartoon_text_filters(LESSON_DIR, "en")
    assert filters and all("x=1460:y=872" not in value for value in filters)
    assert any("white@1.0" in value for value in filters)


def test_lyosha_mila_parrot_and_required_movie_contract_remain_authored():
    lesson = json.loads((LESSON_DIR / "lesson.json").read_text("utf-8"))
    timeline = {item["phrase_id"]: item for item in lesson["timeline"]}
    assert timeline["lesha_clothes"]["floor_y_norm"] == pytest.approx(.82)
    assert timeline["lesha_clothes"]["placement_side"] == "left"
    assert timeline["mila_gift"]["x_norm"] == pytest.approx(.14)
    assert timeline["mila_gift"]["placement_side"] == "left"
    for phrase in ("penguin", "zebra"):
        assert timeline[phrase]["x_norm"] == pytest.approx(.07)
        assert timeline[phrase]["protected_boxes_norm"] == [[.76, .81, .23, .19]]
    required = {item["phrase_id"] for item in lesson["required_phrases"]}
    assert "parrot" in required
    parrot_slide = next(item for item in lesson["slides"] if item["slide_id"] == "slide_44")
    assert parrot_slide["required_phrase_id"] == "parrot"
    assert parrot_slide["allow_skip"] is False
    animal_flow = next(item for item in lesson["slides"] if item["slide_id"] == "slide_46")
    assert "parrot" in {item["phrase_id"] for item in animal_flow["animal_questions"]}
