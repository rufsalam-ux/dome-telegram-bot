from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .character_motion_library import CharacterMotionLibrary, character_key
from .motion_planner import normalize_motion_plan, primary_motion_action, semantic_action


CUTOUT_RIG_VERSION = "avatar-cutout-rig-v2"
CUTOUT_MOTION_VERSION = "avatar-cutout-motion-v2"
_MAX_RIG_SIDE = 512
_FPS = 12

CAPABILITY_KEYS = (
    "canBlink",
    "canAnimateMouth",
    "canMoveHead",
    "canMoveLeftArm",
    "canMoveRightArm",
    "canMoveLeftLeg",
    "canMoveRightLeg",
    "canAnimateTail",
)


def _json_hash(value: object) -> str:
    raw = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, width, height = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if width <= .01 or height <= .01:
        return None
    return left, top, width, height


def _boxes(value: object) -> list[tuple[float, float, float, float]]:
    if not isinstance(value, list):
        return []
    return [parsed for item in value if (parsed := _box(item)) is not None]


def _metadata_rig(metadata: dict[str, Any] | None) -> dict[str, Any]:
    payload = metadata or {}
    return payload.get("rigMetadata") if isinstance(payload.get("rigMetadata"), dict) else {}


def _trusted(metadata: dict[str, Any] | None) -> bool:
    payload = metadata or {}
    return payload.get("userConfirmed") is True or _metadata_rig(payload).get("trusted") is True


def _confidence(metadata: dict[str, Any] | None) -> float:
    try:
        return max(0.0, min(1.0, float((metadata or {}).get("confidence") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _normalized_mapper(
    source_size: tuple[int, int], crop_box: tuple[int, int, int, int], scale: float
):
    source_width, source_height = source_size
    crop_left, crop_top, _crop_right, _crop_bottom = crop_box

    def map_point(value: object) -> tuple[float, float] | None:
        parsed = _point(value)
        if parsed is None:
            return None
        return ((parsed[0] * source_width - crop_left) * scale, (parsed[1] * source_height - crop_top) * scale)

    def map_box(value: object) -> tuple[int, int, int, int] | None:
        parsed = _box(value)
        if parsed is None:
            return None
        x1 = round((parsed[0] * source_width - crop_left) * scale)
        y1 = round((parsed[1] * source_height - crop_top) * scale)
        x2 = round(((parsed[0] + parsed[2]) * source_width - crop_left) * scale)
        y2 = round(((parsed[1] + parsed[3]) * source_height - crop_top) * scale)
        return x1, y1, x2, y2

    return map_point, map_box


def _bounded_box(box: tuple[int, int, int, int] | None, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    if box is None:
        return None
    width, height = size
    x1, y1, x2, y2 = box
    x1, y1 = max(0, min(width - 1, x1)), max(0, min(height - 1, y1))
    x2, y2 = max(x1 + 1, min(width, x2)), max(y1 + 1, min(height, y2))
    return (x1, y1, x2, y2) if x2 - x1 >= 3 and y2 - y1 >= 3 else None


def _expanded_box(box: tuple[int, int, int, int], size: tuple[int, int], ratio: float = .16) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pad_x = max(2, round((x2 - x1) * ratio))
    pad_y = max(2, round((y2 - y1) * ratio))
    return _bounded_box((x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y), size) or box


def _ellipse_mask(size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return mask


def _capsule_mask(
    size: tuple[int, int], start: tuple[float, float], end: tuple[float, float], width: float
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    radius = max(2, round(width / 2))
    a = (round(start[0]), round(start[1]))
    b = (round(end[0]), round(end[1]))
    draw.line((a, b), fill=255, width=max(3, round(width)))
    for x, y in (a, b):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
    return mask


def _rect_mask(size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=max(2, min(box[2] - box[0], box[3] - box[1]) // 4), fill=255)
    return mask


def _safe_part_mask(mask: Image.Image, alpha: Image.Image, total_foreground: int) -> Image.Image | None:
    pixels = (np.asarray(mask, dtype=np.uint8) > 0) & (np.asarray(alpha, dtype=np.uint8) > 20)
    count = int(pixels.sum())
    if count < max(24, round(total_foreground * .0025)) or count > round(total_foreground * .48):
        return None
    return Image.fromarray(pixels.astype(np.uint8) * 255)


def _detect_face_boxes(source: Image.Image, head_box: tuple[int, int, int, int] | None) -> dict[str, list[tuple[int, int, int, int]]]:
    """Conservatively locate existing dark face marks; never synthesize features."""

    if head_box is None:
        return {"eyes": [], "mouth": []}
    x1, y1, x2, y2 = head_box
    crop = np.asarray(source.crop(head_box).convert("RGBA"))
    if crop.size == 0:
        return {"eyes": [], "mouth": []}
    alpha = crop[:, :, 3] > 40
    opaque = crop[:, :, :3][alpha]
    if len(opaque) < 80:
        return {"eyes": [], "mouth": []}
    background = np.median(opaque, axis=0)
    distance = np.linalg.norm(crop[:, :, :3].astype(np.float32) - background.astype(np.float32), axis=2)
    luminance = cv2.cvtColor(crop[:, :, :3], cv2.COLOR_RGB2GRAY)
    median_luminance = float(np.median(luminance[alpha]))
    candidate = alpha & (distance > 48) & (luminance < min(190, median_luminance - 18))
    # Ignore the outer head contour and very thin antialiasing noise.
    margin_x = max(2, round(candidate.shape[1] * .08))
    margin_y = max(2, round(candidate.shape[0] * .08))
    candidate[:margin_y] = False
    candidate[-margin_y:] = False
    candidate[:, :margin_x] = False
    candidate[:, -margin_x:] = False
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(candidate.astype(np.uint8), 8)
    components: list[tuple[tuple[int, int, int, int], float, float, int]] = []
    head_area = max(1, candidate.shape[0] * candidate.shape[1])
    for index in range(1, count):
        left, top, width, height, area = (int(v) for v in stats[index])
        if area < max(4, head_area * .0008) or area > head_area * .09:
            continue
        cx, cy = float(centroids[index][0]), float(centroids[index][1])
        components.append(((x1 + left, y1 + top, x1 + left + width, y1 + top + height), cx, cy, area))
    head_height = max(1, y2 - y1)
    eyes = [row for row in components if row[2] <= head_height * .58]
    eyes.sort(key=lambda row: row[3], reverse=True)
    eye_boxes = [row[0] for row in sorted(eyes[:2], key=lambda row: row[1])]
    mouths = [row for row in components if row[2] >= head_height * .48 and (row[0][2] - row[0][0]) >= 3]
    mouths.sort(key=lambda row: (row[0][2] - row[0][0]) * row[3], reverse=True)
    return {"eyes": eye_boxes, "mouth": [mouths[0][0]] if mouths else []}


def _extract_layer(source: Image.Image, mask: Image.Image) -> Image.Image:
    layer = source.copy()
    layer.putalpha(Image.fromarray(np.minimum(np.asarray(source.getchannel("A")), np.asarray(mask)).astype(np.uint8)))
    return layer


def _dominant_rgb(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return np.array([0, 0, 0], dtype=np.uint8)
    pixels = values.reshape(-1, 3).astype(np.uint8)
    quantized = pixels // 16
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    winner = colors[int(np.argmax(counts))]
    selected = np.all(quantized == winner, axis=1)
    return np.median(pixels[selected], axis=0).astype(np.uint8)


def _feature_layer(head: Image.Image, box: tuple[int, int, int, int], *, trusted: bool = False) -> Image.Image:
    """Extract only visible feature pixels inside a confirmed/detected box."""

    array = np.asarray(head)
    x1, y1, x2, y2 = box
    pad = max(2, round(max(x2 - x1, y2 - y1) * .35))
    rx1, ry1 = max(0, x1 - pad), max(0, y1 - pad)
    rx2, ry2 = min(array.shape[1], x2 + pad), min(array.shape[0], y2 + pad)
    nearby = array[ry1:ry2, rx1:rx2]
    nearby_opaque = nearby[:, :, 3] > 30
    inner_x1, inner_y1 = x1 - rx1, y1 - ry1
    inner_x2, inner_y2 = x2 - rx1, y2 - ry1
    ring = nearby_opaque.copy()
    ring[inner_y1:inner_y2, inner_x1:inner_x2] = False
    background = _dominant_rgb(nearby[:, :, :3][ring if ring.any() else nearby_opaque])
    region = array[y1:y2, x1:x2]
    distance = np.linalg.norm(region[:, :, :3].astype(np.float32) - background.astype(np.float32), axis=2)
    selected = (region[:, :, 3] > 30) & (distance > 24)
    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(selected.astype(np.uint8), 8)
    interior = np.zeros_like(selected)
    region_height, region_width = selected.shape
    ranked_components: list[tuple[float, int, int]] = []
    for index in range(1, component_count):
        left, top, width, height, area = (int(value) for value in stats[index])
        center_x, center_y = centroids[index]
        distance_to_center = math.hypot(center_x / max(1, region_width) - .5, center_y / max(1, region_height) - .5)
        touches_edge = left <= 0 or top <= 0 or left + width >= region_width or top + height >= region_height
        if area >= 2 and (trusted or not touches_edge):
            ranked_components.append((distance_to_center, -area, index))
    if ranked_components:
        best_distance = min(row[0] for row in ranked_components)
        for distance_to_center, _negative_area, index in ranked_components:
            if distance_to_center <= min(.42, best_distance + .16):
                interior |= labels == index
    if interior.any():
        selected = interior
    else:
        return Image.new("RGBA", head.size, (0, 0, 0, 0))
    if int(selected.sum()) < 3:
        return Image.new("RGBA", head.size, (0, 0, 0, 0))
    mask = np.zeros(array.shape[:2], dtype=np.uint8)
    mask[y1:y2, x1:x2] = selected.astype(np.uint8) * 255
    return _extract_layer(head, Image.fromarray(mask))


def _remove_feature_pixels(
    head: Image.Image,
    features: list[tuple[Image.Image, tuple[int, int, int, int]]],
) -> Image.Image:
    if not features:
        return head
    array = np.asarray(head).copy()
    alpha = array[:, :, 3]
    for feature, (x1, y1, x2, y2) in features:
        pad = max(2, round(max(x2 - x1, y2 - y1) * .3))
        rx1, ry1 = max(0, x1 - pad), max(0, y1 - pad)
        rx2, ry2 = min(array.shape[1], x2 + pad), min(array.shape[0], y2 + pad)
        ring = array[ry1:ry2, rx1:rx2, :3]
        ring_alpha = alpha[ry1:ry2, rx1:rx2] > 20
        fill = _dominant_rgb(ring[:, :, :3][ring_alpha]) if ring_alpha.any() else np.array([0, 0, 0], dtype=np.uint8)
        selected = np.asarray(feature.getchannel("A"))[y1:y2, x1:x2] > 20
        array[y1:y2, x1:x2, :3][selected] = fill
    return Image.fromarray(array)


def _part_specifications(metadata: dict[str, Any], map_point, map_box, size: tuple[int, int]) -> dict[str, tuple[Image.Image, tuple[float, float]]]:
    rig = _metadata_rig(metadata)
    regions = rig.get("regions") if isinstance(rig.get("regions"), dict) else {}
    joints = rig.get("joints") if isinstance(rig.get("joints"), dict) else {}
    torso_box = _bounded_box(map_box(metadata.get("torsoBoundingBox") or regions.get("torso")), size)
    torso_center = (
        ((torso_box[0] + torso_box[2]) / 2, (torso_box[1] + torso_box[3]) / 2)
        if torso_box else (size[0] / 2, size[1] * .58)
    )
    specs: dict[str, tuple[Image.Image, tuple[float, float]]] = {}

    head_box = _bounded_box(map_box(metadata.get("headBoundingBox") or regions.get("head")), size)
    neck = map_point(joints.get("neck")) or (torso_center[0], torso_box[1] if torso_box else size[1] * .4)
    if head_box:
        specs["head"] = (_ellipse_mask(size, head_box), neck)

    front_boxes = _boxes(metadata.get("frontLimbs") or regions.get("front_limbs"))
    rear_boxes = _boxes(metadata.get("rearLimbs") or regions.get("rear_limbs"))

    limb_rows = (
        ("left_front_limb", metadata.get("leftArmOrFrontLimb") or joints.get("left_shoulder"), metadata.get("leftHandOrFrontPaw") or joints.get("left_hand_or_paw"), front_boxes[0] if front_boxes else None),
        ("right_front_limb", metadata.get("rightArmOrFrontLimb") or joints.get("right_shoulder"), metadata.get("rightHandOrFrontPaw") or joints.get("right_hand_or_paw"), front_boxes[1] if len(front_boxes) > 1 else None),
        ("left_rear_limb", None, metadata.get("leftLegOrRearLimb") or joints.get("left_hip_or_rear_limb"), rear_boxes[0] if rear_boxes else None),
        ("right_rear_limb", None, metadata.get("rightLegOrRearLimb") or joints.get("right_hip_or_rear_limb"), rear_boxes[1] if len(rear_boxes) > 1 else None),
    )
    for name, start_value, end_value, region in limb_rows:
        region_box = _bounded_box(map_box(region), size) if region else None
        end = map_point(end_value)
        if "rear" in name and end:
            start = (end[0], torso_box[3] - max(2, (torso_box[3] - torso_box[1]) * .06)) if torso_box else (end[0], size[1] * .68)
        else:
            start = map_point(start_value)
        if region_box:
            pivot = start or ((region_box[0] + region_box[2]) / 2, region_box[1])
            specs[name] = (_rect_mask(size, region_box), pivot)
        elif start and end and math.dist(start, end) >= max(5, min(size) * .025):
            specs[name] = (_capsule_mask(size, start, end, max(7, math.dist(start, end) * .48)), start)

    tail_box = _bounded_box(map_box(metadata.get("tailBoundingBox") or regions.get("tail")), size)
    tail_point = map_point(metadata.get("tailPoint") or joints.get("tail"))
    back_point = map_point(metadata.get("backPoint")) or torso_center
    if tail_box:
        center = ((tail_box[0] + tail_box[2]) / 2, (tail_box[1] + tail_box[3]) / 2)
        pivot = min((back_point, center), key=lambda value: math.dist(value, torso_center))
        specs["tail"] = (_rect_mask(size, tail_box), pivot)
    elif tail_point and math.dist(back_point, tail_point) >= max(5, min(size) * .03):
        specs["tail"] = (_capsule_mask(size, back_point, tail_point, max(8, math.dist(back_point, tail_point) * .38)), back_point)
    return specs


def ensure_layered_rig(
    character_png: Path,
    storage_root: Path,
    metadata: dict[str, Any] | None = None,
    *,
    avatar_id: str | int | None = None,
) -> dict[str, Any]:
    """Persist source-pixel cutout layers and per-component capabilities."""

    payload = dict(metadata or {})
    library = CharacterMotionLibrary(storage_root, character_png, avatar_id=avatar_id)
    rig_root = library.root / CUTOUT_RIG_VERSION
    manifest_path = rig_root / "rig.json"
    digest = _json_hash(payload)
    if manifest_path.exists():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = [rig_root / value for value in (cached.get("parts") or {}).values()]
            if cached.get("source_avatar_hash") == library.source_avatar_hash and cached.get("metadata_hash") == digest and files and all(path.exists() for path in files):
                return cached
        except Exception:
            pass

    rig_root.mkdir(parents=True, exist_ok=True)
    with Image.open(character_png) as opened:
        full = opened.convert("RGBA")
    alpha_box = full.getchannel("A").getbbox()
    if alpha_box is None:
        raise ValueError("Avatar has no visible source pixels")
    visible = full.crop(alpha_box)
    scale = min(1.0, _MAX_RIG_SIDE / max(visible.size))
    if scale < 1:
        visible = visible.resize((max(1, round(visible.width * scale)), max(1, round(visible.height * scale))), Image.Resampling.LANCZOS)
    visible.save(rig_root / "source.png")
    map_point, map_box = _normalized_mapper(full.size, alpha_box, scale)
    total_foreground = int((np.asarray(visible.getchannel("A")) > 20).sum())
    specifications = _part_specifications(payload, map_point, map_box, visible.size)
    claimed = Image.new("L", visible.size, 0)
    layers: dict[str, Image.Image] = {}
    pivots: dict[str, list[float]] = {}
    for name in ("head", "left_front_limb", "right_front_limb", "left_rear_limb", "right_rear_limb", "tail"):
        if name not in specifications:
            continue
        candidate, pivot = specifications[name]
        available = Image.fromarray(
            ((np.asarray(candidate) > 0) & (np.asarray(claimed) == 0)).astype(np.uint8) * 255,
        )
        safe = _safe_part_mask(available, visible.getchannel("A"), total_foreground)
        if safe is None:
            continue
        layers[name] = _extract_layer(visible, safe)
        pivots[name] = [round(float(pivot[0]), 3), round(float(pivot[1]), 3)]
        claimed = Image.fromarray(np.maximum(np.asarray(claimed), np.asarray(safe)).astype(np.uint8))

    # Keep a small original-pixel joint cap underneath each moving layer.  It
    # hides rotation seams without inventing or inpainting any character art.
    base_claimed = claimed.copy()
    joint_draw = ImageDraw.Draw(base_claimed)
    joint_radius = max(3, round(min(visible.size) * .018))
    for pivot in pivots.values():
        px, py = round(pivot[0]), round(pivot[1])
        joint_draw.ellipse((px - joint_radius, py - joint_radius, px + joint_radius, py + joint_radius), fill=0)
    base = visible.copy()
    base.putalpha(Image.fromarray(
        np.where(np.asarray(base_claimed) > 0, 0, np.asarray(visible.getchannel("A"))).astype(np.uint8)
    ))

    head_box = _bounded_box(map_box(payload.get("headBoundingBox") or _metadata_rig(payload).get("regions", {}).get("head")), visible.size)
    explicit_eyes = [_bounded_box(map_box(value), visible.size) for value in (payload.get("eyeBoundingBoxes") or [])]
    explicit_eyes = [_expanded_box(box, visible.size) for box in explicit_eyes if box]
    explicit_mouth = _bounded_box(map_box(payload.get("mouthBoundingBox")), visible.size)
    if explicit_mouth:
        explicit_mouth = _expanded_box(explicit_mouth, visible.size)
    detected = _detect_face_boxes(visible, head_box)
    eye_boxes = explicit_eyes or [_expanded_box(box, visible.size) for box in detected["eyes"]]
    mouth_boxes = ([explicit_mouth] if explicit_mouth else [_expanded_box(box, visible.size) for box in detected["mouth"]])
    if "head" in layers:
        original_head = layers["head"]
        trusted_features = _trusted(payload)
        extracted_features: list[tuple[Image.Image, tuple[int, int, int, int]]] = []
        if eye_boxes:
            eye_union = Image.new("RGBA", visible.size, (0, 0, 0, 0))
            for region in eye_boxes:
                feature = _feature_layer(original_head, region, trusted=trusted_features)
                if feature.getchannel("A").getbbox():
                    eye_union = Image.alpha_composite(eye_union, feature)
                    extracted_features.append((feature, region))
            if eye_union.getchannel("A").getbbox():
                layers["eyes"] = eye_union
        if mouth_boxes:
            mouth_feature = _feature_layer(original_head, mouth_boxes[0], trusted=trusted_features)
            if mouth_feature.getchannel("A").getbbox():
                layers["mouth"] = mouth_feature
                extracted_features.append((mouth_feature, mouth_boxes[0]))
        layers["head"] = _remove_feature_pixels(original_head, extracted_features)

    parts: dict[str, str] = {"base": "base.png", "source": "source.png"}
    base.save(rig_root / "base.png")
    for name, layer in layers.items():
        filename = f"{name}.png"
        layer.save(rig_root / filename)
        parts[name] = filename

    trusted = _trusted(payload)
    rich = trusted or _confidence(payload) >= .78
    declared = _metadata_rig(payload).get("capabilities")
    declared = declared if isinstance(declared, dict) else {}
    capability_map = {
        "level1BodyMotion": True,
        "canBlink": rich and "eyes" in layers,
        "canAnimateMouth": rich and "mouth" in layers,
        "canMoveHead": rich and "head" in layers and bool(declared.get("canMoveHead", True)),
        "canMoveLeftArm": rich and "left_front_limb" in layers and bool(declared.get("canMoveLeftArm")),
        "canMoveRightArm": rich and "right_front_limb" in layers and bool(declared.get("canMoveRightArm")),
        "canMoveLeftLeg": rich and "left_rear_limb" in layers and bool(declared.get("canMoveLeftLeg")),
        "canMoveRightLeg": rich and "right_rear_limb" in layers and bool(declared.get("canMoveRightLeg")),
        "canAnimateTail": rich and "tail" in layers and bool(declared.get("canAnimateTail") or declared.get("tailMotion")),
    }
    capability_map.update({
        "canMoveLeftArmOrFrontLimb": capability_map["canMoveLeftArm"],
        "canMoveRightArmOrFrontLimb": capability_map["canMoveRightArm"],
        "canMoveLeftLegOrRearLimb": capability_map["canMoveLeftLeg"],
        "canMoveRightLegOrRearLimb": capability_map["canMoveRightLeg"],
    })
    ground = map_point(payload.get("groundAnchor") or payload.get("feetAnchor")) or (visible.width / 2, visible.height)
    manifest = {
        "version": CUTOUT_RIG_VERSION,
        "character_id": library.avatar_id,
        "avatar_id": library.avatar_id,
        "source_avatar_hash": library.source_avatar_hash,
        "metadata_hash": digest,
        "provider": "local_source_pixel_cutout",
        "source_png": str(character_png),
        "size": [visible.width, visible.height],
        "parts": parts,
        "pivots": pivots,
        "ground_anchor": [round(ground[0], 3), round(ground[1], 3)],
        "canonical_facing": str(payload.get("canonicalFacing") or payload.get("facingDirection") or "FRONT").upper(),
        "feature_regions": {"eyes": [list(box) for box in eye_boxes], "mouth": [list(box) for box in mouth_boxes]},
        "capability_map": capability_map,
        "capabilities": [key for key, enabled in capability_map.items() if enabled],
        "confidence": _confidence(payload),
        "trusted": trusted,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = rig_root / "rig.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)
    return manifest


def _load_layers(manifest: dict[str, Any], rig_root: Path) -> dict[str, Image.Image]:
    return {
        name: Image.open(rig_root / filename).convert("RGBA")
        for name, filename in (manifest.get("parts") or {}).items()
        if name != "source" and (rig_root / filename).exists()
    }


def _rotate(layer: Image.Image, degrees: float, pivot: list[float] | tuple[float, float]) -> Image.Image:
    if abs(degrees) < .01:
        return layer
    return layer.rotate(degrees, resample=Image.Resampling.BICUBIC, center=(float(pivot[0]), float(pivot[1])))


def _scale_feature(layer: Image.Image, box: list[int], vertical_scale: float) -> Image.Image:
    x1, y1, x2, y2 = (int(v) for v in box)
    crop = layer.crop((x1, y1, x2, y2))
    if crop.width < 2 or crop.height < 2:
        return layer
    new_height = max(1, round(crop.height * max(.12, vertical_scale)))
    resized = crop.resize((crop.width, new_height), Image.Resampling.BICUBIC)
    output = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    output.alpha_composite(resized, (x1, round((y1 + y2 - new_height) / 2)))
    return output


def _scale_about_ground(image: Image.Image, vertical_scale: float, ground: list[float]) -> Image.Image:
    if abs(vertical_scale - 1.0) < .001:
        return image
    new_height = max(1, round(image.height * vertical_scale))
    resized = image.resize((image.width, new_height), Image.Resampling.BICUBIC)
    output = Image.new("RGBA", image.size, (0, 0, 0, 0))
    top = round(float(ground[1]) - float(ground[1]) * vertical_scale)
    output.alpha_composite(resized, (0, top))
    return output


def _translate(image: Image.Image, dx: int = 0, dy: int = 0) -> Image.Image:
    if dx == 0 and dy == 0:
        return image
    output = Image.new("RGBA", image.size, (0, 0, 0, 0))
    output.alpha_composite(image, (dx, dy))
    return output


def action_has_visible_animation(manifest: dict[str, Any], action: str) -> bool:
    """Strict capability matrix: translation alone never passes blink/wave/walk."""

    caps = manifest.get("capability_map") or {}
    key = semantic_action(action)
    if key == "blink":
        return bool(caps.get("canBlink"))
    if key == "wave":
        return bool(caps.get("canMoveLeftArm") or caps.get("canMoveRightArm"))
    if key == "point":
        return bool(caps.get("canMoveLeftArm") or caps.get("canMoveRightArm"))
    if key in {"walk", "walk_left", "walk_right"}:
        return bool(caps.get("canMoveLeftLeg") or caps.get("canMoveRightLeg"))
    if key in {"tail_idle", "tail_sway"}:
        return bool(caps.get("canAnimateTail"))
    if key == "talk":
        return bool(caps.get("canAnimateMouth") or caps.get("canMoveHead") or caps.get("level1BodyMotion"))
    return key in {
        "idle", "listen", "turn", "turn_left", "turn_right", "happy", "thinking", "small_jump",
        "enter_left", "enter_right", "exit_left", "exit_right", "tail_idle", "tail_sway", "point",
    }


def render_rig_frame(
    manifest: dict[str, Any],
    rig_root: Path,
    action: str,
    progress: float,
    *,
    amplitude: float | None = None,
    direction: str = "front",
    _layers: dict[str, Image.Image] | None = None,
) -> Image.Image:
    """Render one source-pixel frame; no pixels are generated or redrawn."""

    layers = _layers or _load_layers(manifest, rig_root)
    base = layers.get("base") or Image.open(rig_root / "source.png").convert("RGBA")
    phase = max(0.0, min(1.0, progress)) * math.tau
    key = semantic_action(action)
    speaking = amplitude is not None
    caps = manifest.get("capability_map") or {}
    pivots = manifest.get("pivots") or {}
    output = base.copy()

    front_angle = rear_angle = tail_angle = head_angle = 0.0
    if key in {"walk", "walk_left", "walk_right", "enter_left", "enter_right", "exit_left", "exit_right"}:
        front_angle = 9.0 * math.sin(phase)
        rear_angle = -10.0 * math.sin(phase)
        tail_angle = 4.0 * math.sin(phase)
        head_angle = 1.5 * math.sin(phase)
    elif key == "wave":
        # Raise one confirmed front limb and oscillate it around the shoulder.
        front_angle = -118.0 + 14.0 * math.sin(phase * 2)
        head_angle = 2.0 * math.sin(phase)
    elif key == "point":
        front_angle = -14.0 + 2.0 * math.sin(phase)
    elif key == "talk":
        active = 0.0 if amplitude is None else max(0.0, min(1.0, amplitude))
        head_angle = (1.2 + 2.0 * active) * math.sin(phase * 1.5)
        front_angle = 2.0 * active * math.sin(phase)
        tail_angle = 2.5 * math.sin(phase)
    elif key in {"listen", "thinking"}:
        head_angle = 3.0 + 1.2 * math.sin(phase)
    elif key == "happy":
        head_angle = 2.4 * math.sin(phase * 2)
        front_angle = -8.0 - 5.0 * abs(math.sin(phase))
        tail_angle = 7.0 * math.sin(phase * 2)
    elif key in {"tail_idle", "tail_sway"}:
        tail_angle = (4.0 if key == "tail_idle" else 8.0) * math.sin(phase)
    else:
        head_angle = 1.1 * math.sin(phase)
        tail_angle = 2.5 * math.sin(phase)
    if speaking and key != "talk":
        active = max(0.0, min(1.0, float(amplitude or 0.0)))
        head_angle += (1.0 + active) * math.sin(phase * 1.5)

    part_order = ("left_rear_limb", "right_rear_limb", "tail", "left_front_limb", "right_front_limb")
    for index, name in enumerate(part_order):
        layer = layers.get(name)
        pivot = pivots.get(name)
        if layer is None or pivot is None:
            continue
        angle = 0.0
        if name == "tail" and caps.get("canAnimateTail"):
            angle = tail_angle
        elif "rear_limb" in name and caps.get(
            "canMoveLeftLeg" if name.startswith("left") else "canMoveRightLeg"
        ):
            angle = rear_angle * (1 if index % 2 == 0 else -1)
        elif "front_limb" in name and caps.get(
            "canMoveLeftArm" if name.startswith("left") else "canMoveRightArm"
        ):
            if key == "wave":
                selected = "right_front_limb" if caps.get("canMoveRightArm") else "left_front_limb"
                angle = front_angle if name == selected else 0.0
            else:
                angle = front_angle * (1 if name.startswith("right") else -1)
        output = Image.alpha_composite(output, _rotate(layer, angle, pivot))

    head = layers.get("head")
    if head is not None:
        head_group = head.copy()
        eyes = layers.get("eyes")
        eye_boxes = (manifest.get("feature_regions") or {}).get("eyes") or []
        if eyes is not None:
            blink_amount = 1.0
            if caps.get("canBlink") and (key == "blink" or key in {"idle", "listen", "talk"}):
                pulse = abs(math.sin(phase if key == "blink" else phase * 1.6))
                blink_amount = max(.14, 1.0 - pulse ** 12 * .86)
            animated_eyes = Image.new("RGBA", eyes.size, (0, 0, 0, 0))
            for region in eye_boxes:
                animated_eyes = Image.alpha_composite(animated_eyes, _scale_feature(eyes, region, blink_amount))
            head_group = Image.alpha_composite(head_group, animated_eyes)
        mouth = layers.get("mouth")
        mouth_boxes = (manifest.get("feature_regions") or {}).get("mouth") or []
        if mouth is not None and mouth_boxes:
            mouth_scale = 1.0
            if speaking and caps.get("canAnimateMouth"):
                mouth_scale = .62 + .95 * max(0.0, min(1.0, amplitude))
            head_group = Image.alpha_composite(head_group, _scale_feature(mouth, mouth_boxes[0], mouth_scale))
        if caps.get("canMoveHead"):
            head_group = _rotate(head_group, head_angle, pivots.get("head") or manifest.get("ground_anchor") or [head.width / 2, head.height / 2])
        output = Image.alpha_composite(output, head_group)

    ground = manifest.get("ground_anchor") or [output.width / 2, output.height]
    breathing = 1.0 + .009 * math.sin(phase)
    if speaking:
        breathing += .006 * max(0.0, min(1.0, amplitude))
    output = _scale_about_ground(output, breathing, ground)
    if key in {"happy", "small_jump"}:
        output = _translate(output, dy=-round(5 * abs(math.sin(phase))))
    elif key in {"walk", "walk_left", "walk_right", "enter_left", "enter_right", "exit_left", "exit_right"}:
        output = _translate(output, dy=-round(2.5 * abs(math.sin(phase * 2))))
    elif key in {"thinking", "listen"}:
        output = _rotate(output, 1.2 * math.sin(phase), ground)

    source_facing = str(manifest.get("canonical_facing") or "FRONT").lower()
    desired = str(direction or "front").lower()
    if desired in {"left", "right"} and source_facing in {"left", "right"} and desired != source_facing:
        output = output.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return output


def _audio_envelope(audio_path: Path, duration: float, fps: int, talk_offset: float, ffmpeg_bin: str) -> list[float | None]:
    frame_count = max(2, round(duration * fps))
    samples = np.array([], dtype=np.float32)
    sample_rate = 8000
    if audio_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(audio_path), "rb") as stream:
                sample_rate = stream.getframerate()
                width = stream.getsampwidth()
                channels = stream.getnchannels()
                raw = stream.readframes(stream.getnframes())
            if width == 2:
                values = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                if channels > 1:
                    values = values.reshape(-1, channels).mean(axis=1)
                samples = values / 32768.0
        except Exception:
            samples = np.array([], dtype=np.float32)
    if len(samples) == 0:
        try:
            decoded = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1", "-ar", "8000", "pipe:1"],
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
            samples = np.frombuffer(decoded, dtype=np.int16).astype(np.float32) / 32768.0
            sample_rate = 8000
        except Exception:
            samples = np.array([], dtype=np.float32)
    result: list[float | None] = []
    for frame in range(frame_count):
        local_time = frame / fps - max(0.0, talk_offset)
        start = round(local_time * sample_rate)
        end = start + max(1, round(sample_rate / fps))
        if start < 0 or start >= len(samples):
            result.append(None)
            continue
        window = samples[max(0, start):min(len(samples), end)]
        if len(window) == 0:
            result.append(None)
            continue
        rms = float(np.sqrt(np.mean(window * window)))
        result.append(None if rms < .006 else max(.08, min(1.0, rms * 11.0)))
    return result


def ensure_local_animation_clip(
    character_png: Path,
    segment: dict[str, Any],
    storage_root: Path,
    work_root: Path,
    ffmpeg_bin: str,
    metadata: dict[str, Any] | None = None,
    audio_path: Path | None = None,
) -> Path | None:
    """Return a real locally-rendered alpha clip, cached unless voice-specific."""

    manifest = ensure_layered_rig(character_png, storage_root, metadata)
    plan = normalize_motion_plan(segment)
    action = primary_motion_action(segment)
    plan_actions = [command.action for command in plan.commands] or [action]
    if not any(action_has_visible_animation(manifest, item) for item in plan_actions):
        return None
    duration = max(.5, float(segment.get("end", 1.0)) - float(segment.get("visible_start", 0.0)))
    desired = str(segment.get("resolved_facing") or "").lower()
    if desired not in {"left", "right", "front"}:
        desired = "left" if action.endswith("_left") else "right" if action.endswith("_right") else "front"
    library = CharacterMotionLibrary(storage_root, character_png)
    voice_specific = audio_path is not None and audio_path.exists()
    multi_action = len(plan.commands) > 1
    loop_duration = duration if voice_specific or multi_action else 2.0
    if not voice_specific and not multi_action:
        cached = library.find_compatible(
            action, direction=desired, duration=loop_duration,
            generation_version=CUTOUT_MOTION_VERSION,
        )
        if cached:
            return cached
    rig_root = library.root / CUTOUT_RIG_VERSION
    layers = _load_layers(manifest, rig_root)
    frame_count = max(8, round(loop_duration * _FPS))
    talk_offset = max(0.0, float(segment.get("talk_start", segment.get("visible_start", 0.0))) - float(segment.get("visible_start", 0.0)))
    envelope = (
        _audio_envelope(audio_path, loop_duration, _FPS, talk_offset, ffmpeg_bin)
        if voice_specific and audio_path is not None else
        [(.18 + .72 * abs(math.sin(index / max(1, frame_count - 1) * math.tau * 2)) if action == "talk" else None) for index in range(frame_count)]
    )
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cutout-frames-", dir=str(work_root)) as temp_name:
        frames = Path(temp_name)
        for index in range(frame_count):
            absolute_time = float(segment.get("visible_start", 0.0)) + index / _FPS
            frame_action = action
            for command in plan.commands:
                if command.start <= absolute_time < command.start + command.duration:
                    frame_action = command.action
                    break
            frame = render_rig_frame(
                manifest, rig_root, frame_action, index / max(1, frame_count - 1),
                amplitude=envelope[index], direction=desired, _layers=layers,
            )
            frame.save(frames / f"frame_{index:04d}.png")
        output = work_root / f"local-{action}-{desired}-{_json_hash([manifest['metadata_hash'], str(audio_path) if voice_specific else 'loop'])}.mov"
        try:
            subprocess.run(
                [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(_FPS),
                 "-i", str(frames / "frame_%04d.png"), "-c:v", "qtrle", "-pix_fmt", "argb", str(output)],
                check=True,
                capture_output=True,
                timeout=max(45, round(loop_duration * 8)),
            )
        except Exception:
            output.unlink(missing_ok=True)
            return None
    if not output.exists() or output.stat().st_size < 1_000:
        return None
    if voice_specific or multi_action:
        return output
    signature = hashlib.sha256(
        f"{CUTOUT_MOTION_VERSION}|{manifest['metadata_hash']}|{action}|{desired}".encode("utf-8")
    ).hexdigest()[:24]
    registered = library.register(
        signature, output, description_ru="local source-pixel cutout animation",
        speaking=action == "talk", view=f"side_{desired}" if desired in {"left", "right"} else "front",
        duration=loop_duration, provider="local_cutout", animation_key=action, direction=desired,
        transparent_background=True, generation_version=CUTOUT_MOTION_VERSION,
    )
    output.unlink(missing_ok=True)
    return registered


def animation_capability_matrix(manifests: dict[str, dict[str, Any]]) -> dict[str, dict[str, bool]]:
    actions = ("idle", "blink", "talk", "wave", "walk", "turn", "happy", "enter_left", "exit_right")
    return {
        action: {name: action_has_visible_animation(manifest, action) for name, manifest in manifests.items()}
        for action in actions
    }
