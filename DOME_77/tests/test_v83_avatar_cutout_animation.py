from __future__ import annotations

import math
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from app.services.animation_engine.cutout_renderer import (
    CUTOUT_RIG_VERSION,
    _audio_envelope,
    action_has_visible_animation,
    animation_capability_matrix,
    ensure_layered_rig,
    ensure_local_animation_clip,
    render_rig_frame,
)
from app.services.character_geometry import attach_character_rig
from app.services.preset_characters import preset_character_geometry, preset_character_path


def _four_leg_dinosaur(path: Path) -> None:
    image = Image.new("RGBA", (500, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body = (58, 155, 92, 255)
    head = (72, 181, 108, 255)
    draw.ellipse((18, 45, 155, 170), fill=head)
    draw.polygon([(22, 95), (2, 112), (42, 122)], fill=head)
    draw.ellipse((128, 92, 370, 260), fill=body)
    draw.polygon([(340, 128), (490, 174), (355, 205)], fill=body)
    # Four visually separate legs: front limbs stay near the head, rear limbs near the tail.
    for left in (145, 205, 285, 340):
        draw.rounded_rectangle((left, 220, left + 34, 307), radius=10, fill=body)
    draw.ellipse((65, 76, 86, 98), fill=(245, 248, 244, 255))
    draw.ellipse((72, 80, 80, 94), fill=(18, 29, 20, 255))
    draw.arc((42, 105, 112, 145), 10, 145, fill=(20, 55, 30, 255), width=5)
    image.save(path)


def _dinosaur_metadata(*, confidence: float = .96, confirmed: bool = True) -> dict:
    return attach_character_rig({
        "characterBoundingBox": [.004, .14, .976, .82],
        "sourceWidth": 500,
        "sourceHeight": 320,
        "visibleAspectRatio": 1.86,
        "headCenterX": .17,
        "headCenterY": .33,
        "headPoint": [.17, .33],
        "headBoundingBox": [.025, .14, .285, .40],
        "eyeBoundingBoxes": [[.13, .235, .045, .075]],
        "mouthBoundingBox": [.08, .33, .15, .15],
        "bodyCenterX": .52,
        "bodyCenterY": .55,
        "torsoBoundingBox": [.25, .29, .49, .52],
        "frontSide": "LEFT",
        "backSide": "RIGHT",
        "frontPoint": [.02, .35],
        "backPoint": [.72, .52],
        "frontLimbs": [[.28, .68, .09, .29], [.40, .68, .09, .29]],
        "rearLimbs": [[.56, .68, .09, .29], [.68, .68, .09, .29]],
        "leftArmOrFrontLimb": [.325, .69],
        "rightArmOrFrontLimb": [.445, .69],
        "leftHandOrFrontPaw": [.325, .95],
        "rightHandOrFrontPaw": [.445, .95],
        "leftLegOrRearLimb": [.605, .95],
        "rightLegOrRearLimb": [.725, .95],
        "feetAnchor": [.52, .965],
        "groundAnchor": [.52, .965],
        "tailBoundingBox": [.68, .37, .30, .29],
        "tailPoint": [.96, .54],
        "facingDirection": "LEFT",
        "canonicalFacing": "LEFT",
        "confidence": confidence,
        "source": "test_confirmed_non_human",
        "userConfirmed": confirmed,
    }, trusted=confirmed)


def _changed(first: Image.Image, second: Image.Image) -> bool:
    return ImageChops.difference(first.convert("RGBA"), second.convert("RGBA")).getbbox() is not None


def test_custom_high_confidence_rig_persists_independent_non_human_parts(tmp_path: Path):
    source = tmp_path / "dinosaur.png"
    _four_leg_dinosaur(source)
    metadata = _dinosaur_metadata()
    first = ensure_layered_rig(source, tmp_path, metadata, avatar_id="dino-child")
    second = ensure_layered_rig(source, tmp_path, metadata, avatar_id="dino-child")
    assert first["version"] == CUTOUT_RIG_VERSION
    assert first["metadata_hash"] == second["metadata_hash"]
    assert first["created_at"] == second["created_at"]
    assert first["canonical_facing"] == "LEFT"
    assert set(first["parts"]) >= {
        "base", "head", "left_front_limb", "right_front_limb",
        "left_rear_limb", "right_rear_limb", "tail", "eyes", "mouth",
    }
    assert all(first["capability_map"][key] for key in (
        "canBlink", "canAnimateMouth", "canMoveHead", "canMoveLeftArm",
        "canMoveRightArm", "canMoveLeftLeg", "canMoveRightLeg", "canAnimateTail",
    ))


def test_blink_talk_wave_walk_and_tail_are_real_pixel_animation(tmp_path: Path):
    source = tmp_path / "dinosaur.png"
    _four_leg_dinosaur(source)
    manifest = ensure_layered_rig(source, tmp_path, _dinosaur_metadata(), avatar_id="dino-actions")
    rig_root = tmp_path / "children-motion-library/dino-actions" / CUTOUT_RIG_VERSION
    pairs = {
        "idle": (dict(progress=0.0), dict(progress=.25)),
        "blink": (dict(progress=0.0), dict(progress=.25)),
        "talk": (dict(progress=.1, amplitude=.12), dict(progress=.35, amplitude=.95)),
        "wave": (dict(progress=0.0), dict(progress=.125)),
        "walk_left": (dict(progress=0.0, direction="left"), dict(progress=.25, direction="left")),
        "turn_right": (dict(progress=0.0, direction="left"), dict(progress=.25, direction="right")),
        "happy": (dict(progress=0.0), dict(progress=.25)),
        "enter_left": (dict(progress=0.0, direction="right"), dict(progress=.25, direction="right")),
        "exit_right": (dict(progress=0.0, direction="right"), dict(progress=.25, direction="right")),
        "tail_sway": (dict(progress=0.0), dict(progress=.25)),
    }
    for action, (before, after) in pairs.items():
        assert _changed(
            render_rig_frame(manifest, rig_root, action, **before),
            render_rig_frame(manifest, rig_root, action, **after),
        ), action
    assert action_has_visible_animation(manifest, "blink") is True
    assert action_has_visible_animation(manifest, "wave") is True
    assert action_has_visible_animation(manifest, "walk_left") is True


def test_low_confidence_downgrades_each_unsafe_component_not_level_one(tmp_path: Path):
    source = tmp_path / "uncertain.png"
    _four_leg_dinosaur(source)
    manifest = ensure_layered_rig(
        source, tmp_path, _dinosaur_metadata(confidence=.31, confirmed=False), avatar_id="uncertain-child",
    )
    caps = manifest["capability_map"]
    assert caps["level1BodyMotion"] is True
    assert not any(caps[key] for key in (
        "canBlink", "canAnimateMouth", "canMoveHead", "canMoveLeftArm",
        "canMoveRightArm", "canMoveLeftLeg", "canMoveRightLeg", "canAnimateTail",
    ))
    assert action_has_visible_animation(manifest, "idle") is True
    assert action_has_visible_animation(manifest, "talk") is True  # visible body talk fallback, not translation
    assert action_has_visible_animation(manifest, "blink") is False
    assert action_has_visible_animation(manifest, "wave") is False
    assert action_has_visible_animation(manifest, "walk_left") is False
    rig_root = tmp_path / "children-motion-library/uncertain-child" / CUTOUT_RIG_VERSION
    assert _changed(
        render_rig_frame(manifest, rig_root, "talk", .0, amplitude=.1),
        render_rig_frame(manifest, rig_root, "talk", .25, amplitude=.9),
    )


def test_builtin_and_custom_animation_capability_matrix_is_strict(tmp_path: Path):
    builtin_source = preset_character_path("explorer")
    builtin = ensure_layered_rig(builtin_source, tmp_path, preset_character_geometry("explorer"), avatar_id="builtin-explorer")
    custom_source = tmp_path / "dinosaur.png"
    _four_leg_dinosaur(custom_source)
    high = ensure_layered_rig(custom_source, tmp_path, _dinosaur_metadata(), avatar_id="custom-high")
    low = ensure_layered_rig(custom_source, tmp_path, _dinosaur_metadata(confidence=.2, confirmed=False), avatar_id="custom-low")
    matrix = animation_capability_matrix({"builtin": builtin, "custom_high": high, "custom_low": low})
    for action in ("idle", "talk", "turn", "happy", "enter_left", "exit_right"):
        assert all(matrix[action].values()), action
    for action in ("blink", "wave", "walk"):
        assert matrix[action] == {"builtin": True, "custom_high": True, "custom_low": False}


def test_child_voice_envelope_starts_talk_and_returns_to_idle(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    rate = 8000
    samples = np.zeros(rate, dtype=np.int16)
    timeline = np.arange(rate // 2, dtype=np.float32) / rate
    samples[rate // 2:] = (np.sin(2 * math.pi * 220 * timeline) * 9000).astype(np.int16)
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    envelope = _audio_envelope(audio, 1.4, 10, .2, "ffmpeg")
    assert envelope[0] is None and envelope[3] is None
    assert any(value is not None and value > .1 for value in envelope[7:12])
    assert envelope[-1] is None


def test_local_alpha_clip_is_decodable_and_cache_reused(tmp_path: Path):
    ffmpeg = os.getenv("DOME_FFMPEG_BIN") or shutil.which("ffmpeg")
    if not ffmpeg:
        import pytest
        pytest.skip("DOME_FFMPEG_BIN is required for local alpha-video integration")
    source = preset_character_path("explorer")
    metadata = preset_character_geometry("explorer")
    segment = {
        "visible_start": 0, "end": 2, "resolved_facing": "front",
        "actions": [{"action": "WAVE", "start": 0, "duration": 2}],
    }
    first = ensure_local_animation_clip(source, segment, tmp_path, tmp_path / "work-1", ffmpeg, metadata)
    assert first and first.exists() and first.stat().st_size > 10_000 and first.suffix == ".mov"
    modified = first.stat().st_mtime_ns
    second = ensure_local_animation_clip(source, segment, tmp_path, tmp_path / "work-2", ffmpeg, metadata)
    assert second == first and second.stat().st_mtime_ns == modified
    frame = tmp_path / "wave-frame.png"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.6", "-i", str(first), "-frames:v", "1", str(frame)],
        check=True, capture_output=True, timeout=30,
    )
    with Image.open(frame) as rendered:
        assert rendered.mode == "RGBA"
        assert rendered.getchannel("A").getextrema() == (0, 255)
