import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.db.models import Character
from app.services.cartoon_builder import (
    MOVIE_AVATAR_PERCEPTUAL_SCALE,
    _desired_facing,
    _resolve_normalized_timeline,
    _scheduled_voice_duration,
    _should_hflip,
    _visible_character_asset,
)
from app.services.cartoon_text_overlay import cartoon_text_filters, overlay_safe_zone_violations
from app.services.character_geometry import (
    ANALYSIS_VERSION,
    RIG_METADATA_VERSION,
    analyze_character_geometry,
    confirm_character_geometry,
    geometry_from_json,
    geometry_status,
    upgrade_character_geometry_payload,
)
from app.services.preset_characters import preset_character_geometry
from app.services.lesson_runtime import VOICE_FEEDBACK_STATES, classify_voice_feedback
from app.services.animation_engine.character_motion_library import CharacterMotionLibrary
from app.services.animation_engine.local_motion_cache import LOCAL_MOTION_VERSION, ensure_local_motion_cache, local_motion_parameters, safe_fallback_required
from app.services.animation_engine.motion_planner import SEMANTIC_ACTIONS, normalize_motion_plan, semantic_action
from app.services.animation_engine.runtime_provider import prepare_character_animation
from app.services.authored_content import _validate_pre_slide_video


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
    assert payload["sourceWidth"] == 400
    assert payload["sourceHeight"] == 400
    assert payload["visibleAspectRatio"] > 0
    assert payload["headPoint"][0] < payload["bodyCenterX"]
    assert payload["frontSide"] == "LEFT" and payload["backSide"] == "RIGHT"
    assert payload["feetAnchor"] == payload["groundAnchor"]
    assert payload["tailPoint"][0] > payload["headPoint"][0]
    assert payload["leftArmOrFrontLimb"] and payload["rightArmOrFrontLimb"]
    assert payload["leftHandOrFrontPaw"] and payload["rightHandOrFrontPaw"]
    assert payload["leftLegOrRearLimb"] and payload["rightLegOrRearLimb"]
    assert payload["metadataVersion"] == RIG_METADATA_VERSION
    assert payload["rigMetadata"]["joints"]["head"] == payload["headPoint"]
    assert payload["rigMetadata"]["joints"]["ground"] == payload["groundAnchor"]
    assert payload["rigMetadata"]["capabilities"]["talk"] is True
    assert geometry_status(geometry) == "READY"
    assert geometry_from_json(json.dumps(payload)) == payload
    assert set(Character.__table__.columns.keys()) >= {
        "visual_metadata_json", "visual_analysis_version", "visual_analysis_status"
    }

    confirmed = confirm_character_geometry(payload, {
        "headPoint": [.16, .2], "frontPoint": [.04, .25], "backPoint": [.92, .61],
        "feetAnchor": [.52, .96], "tailPoint": [.91, .57], "facingDirection": "LEFT",
    })
    assert confirmed["userConfirmed"] is True
    assert confirmed["analysisVersion"] == ANALYSIS_VERSION
    assert confirmed["headCenterX"] == pytest.approx(.16)
    assert confirmed["groundAnchor"] == [.52, .96]
    assert confirmed["canonicalFacing"] == "LEFT"
    assert confirmed["confirmedAt"]
    assert confirmed["rigMetadata"]["trusted"] is True
    assert confirmed["rigMetadata"]["joints"]["head"] == [.16, .2]
    assert geometry_status(confirmed) == "CONFIRMED"


def test_confirmed_legacy_metadata_migrates_without_losing_head_left_truth():
    legacy={"analysisVersion":"character-geometry-v2","userConfirmed":True,"characterBoundingBox":[0,.05,.98,.9],"headPoint":[.15,.2],"tailPoint":[.9,.55],"feetAnchor":[.5,.95],"facingDirection":"UNKNOWN","confidence":.91}
    upgraded=upgrade_character_geometry_payload(legacy)
    assert upgraded["analysisVersion"]==ANALYSIS_VERSION
    assert upgraded["userConfirmed"] is True
    assert upgraded["canonicalFacing"]==upgraded["facingDirection"]=="LEFT"
    for key in ("leftArmOrFrontLimb","rightArmOrFrontLimb","leftHandOrFrontPaw","rightHandOrFrontPaw","leftLegOrRearLimb","rightLegOrRearLimb"):
        assert len(upgraded[key])==2
    assert upgraded["rigMetadata"]["trusted"] is True
    assert upgraded["rigMetadata"]["joints"]["head"] == [.15, .2]


def test_builtin_avatar_has_trusted_canonical_rig_without_confirmation():
    preset = preset_character_geometry("cat")
    assert preset["userConfirmed"] is True
    assert preset["metadataVersion"] == RIG_METADATA_VERSION
    assert preset["rigMetadata"]["trusted"] is True
    assert preset["rigMetadata"]["mode"] == "cutout_2d"
    assert set(preset["rigMetadata"]["joints"]) >= {"head", "torso", "left_shoulder", "right_shoulder", "left_hip_or_rear_limb", "right_hip_or_rear_limb", "ground"}


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


def test_avatar_animation_library_is_cache_first_and_versioned(tmp_path):
    avatar=tmp_path/"avatar.png";_head_left_dinosaur(avatar);clip=tmp_path/"talk.mp4";clip.write_bytes(b"x"*12000)
    library=CharacterMotionLibrary(tmp_path,avatar,avatar_id=77)
    saved=library.register("sig",clip,description_ru="Говорит",speaking=True,view="side_right",duration=5,animation_key="talk",direction="right",generation_version="avatar-motion-v1")
    assert saved.exists() and saved.stat().st_size==12000
    assert library.find_compatible("talk",direction="right",duration=5,generation_version="avatar-motion-v1")==saved
    manifest=json.loads(library.manifest_path.read_text("utf-8"));item=manifest["motions"]["sig"]
    assert manifest["version"]==2 and manifest["avatar_id"]=="77"
    for key in ("avatar_id","source_avatar_hash","animation_key","direction","duration","transparent_background","asset_uri","generation_version","created_at"):
        assert key in item


def test_semantic_animation_vocabulary_is_complete_and_head_first():
    required={"idle","blink","talk","listen","walk_left","walk_right","turn_left","turn_right","wave","point","happy","thinking","small_jump","enter_left","enter_right","exit_left","exit_right"}
    assert required <= SEMANTIC_ACTIONS
    plan=normalize_motion_plan({"actions":[{"action":"ENTER_LEFT","duration":2},{"action":"TALK","duration":3},{"action":"WAVE","duration":1}]})
    assert [command.action for command in plan.commands]==["enter_left","talk","wave"]
    assert semantic_action("WALK_TO_PARTNER")=="walk_right"
    assert _desired_facing({"actions":[{"action":"ENTER_LEFT"}]})=="RIGHT"
    assert _desired_facing({"actions":[{"action":"EXIT_LEFT"}]})=="LEFT"


def test_local_rig_parameters_are_persistent_cache_first(tmp_path):
    avatar=tmp_path/"avatar.png";_head_left_dinosaur(avatar)
    metadata={"confidence":.9,"tailPoint":[.9,.55],"rigMetadata":{"capabilities":{"safeWholeBodyFallback":False,"canAnimateMouth":True,"canAnimateTail":True}}}
    library,first_hits,first_created=ensure_local_motion_cache(avatar,tmp_path,metadata,avatar_id=77)
    again,second_hits,second_created=ensure_local_motion_cache(avatar,tmp_path,metadata,avatar_id=77)
    assert first_hits==0 and first_created>=len(SEMANTIC_ACTIONS)-1
    assert second_created==0 and second_hits==first_created
    talk=again.find_parameters("talk",direction="front",generation_version=LOCAL_MOTION_VERSION)
    assert talk and talk["mouth_pulse"] is True and talk["whole_body_fallback"] is False
    manifest=json.loads(library.manifest_path.read_text("utf-8"));assert any(str(item.get("asset_uri","")).startswith("rig://") for item in manifest["motions"].values())


def test_low_confidence_rig_uses_non_deforming_motion_fallback(tmp_path):
    metadata={"confidence":.3,"rigMetadata":{"capabilities":{"safeWholeBodyFallback":True}}}
    assert safe_fallback_required(metadata) is True
    wave=local_motion_parameters("wave",tmp_path/"profiles",metadata)
    assert wave["whole_body_fallback"] is True and wave["gesture"] is None and wave["limb_cycle"] is False


def test_animation_feature_flag_preserves_static_png_fallback(monkeypatch,tmp_path):
    avatar=tmp_path/"avatar.png";_head_left_dinosaur(avatar)
    monkeypatch.setattr("app.services.animation_engine.runtime_provider.settings.avatar_animation_engine_enabled",False)
    assert prepare_character_animation(avatar,{"visible_start":0,"end":5,"actions":[{"action":"TALK"}]},tmp_path,allow_generate=False) is None


def test_movie_avatar_has_separate_larger_scale_and_preserves_ground_anchor():
    assert MOVIE_AVATAR_PERCEPTUAL_SCALE>1.12
    timeline=[{"height_norm":.4,"floor_y_norm":.9,"x_norm":.1,"visible_start":0,"end":5}]
    placed=_resolve_normalized_timeline(timeline,1000,1000,character_aspect=.6,ground_ratio=.95)[0]
    assert placed["height"]>round(.4*1000*1.12)
    assert placed["y"]+round(placed["height"]*.95)==900


def test_pre_slide_video_content_contract_is_optional_and_strict():
    assert _validate_pre_slide_video({},"slide 1")==[]
    good={"preSlideVideo":{"enabled":True,"uri":"media/intro.mp4","skippable":True,"showPolicy":"once_ever","autoplay":True}}
    assert _validate_pre_slide_video(good,"slide 1")==[]
    assert any("needs uri" in error for error in _validate_pre_slide_video({"preSlideVideo":{"enabled":True}},"slide 1"))
    assert any("showPolicy" in error for error in _validate_pre_slide_video({"preSlideVideo":{"uri":"x.mp4","showPolicy":"sometimes"}},"slide 1"))


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
    assert timeline["lesha_clothes"]["height_norm"] >= .44
    assert timeline["mila_gift"]["x_norm"] == pytest.approx(.14)
    assert timeline["mila_gift"]["placement_side"] == "left"
    assert timeline["mila_gift"]["height_norm"] >= .42
    for phrase in ("penguin", "zebra"):
        assert timeline[phrase]["x_norm"] == pytest.approx(.07)
        assert timeline[phrase]["protected_boxes_norm"] == [[.76, .81, .23, .19]]
    required = {item["phrase_id"] for item in lesson["required_phrases"]}
    assert "parrot" in required
    parrot_slide = next(item for item in lesson["slides"] if item["slide_id"] == "slide_44")
    assert parrot_slide["required_phrase_id"] == "parrot"
    assert parrot_slide["requiredForMovie"] is True
    assert parrot_slide["allow_skip"] is False
    animal_flow = next(item for item in lesson["slides"] if item["slide_id"] == "slide_46")
    assert "parrot" in {item["phrase_id"] for item in animal_flow["animal_questions"]}
