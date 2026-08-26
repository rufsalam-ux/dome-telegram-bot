from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from app.core.config import settings


log = logging.getLogger("dome.character_geometry")
ANALYSIS_VERSION = "character-geometry-v2"
FACING_DIRECTIONS = {"LEFT", "RIGHT", "FRONT", "UNKNOWN"}


@dataclass(frozen=True)
class CharacterGeometry:
    characterBoundingBox: list[float]
    sourceWidth: int
    sourceHeight: int
    visibleAspectRatio: float
    headCenterX: float
    headCenterY: float
    headPoint: list[float]
    headBoundingBox: list[float] | None
    bodyCenterX: float
    bodyCenterY: float
    torsoBoundingBox: list[float]
    frontSide: str
    backSide: str
    frontPoint: list[float]
    backPoint: list[float]
    frontLimbs: list[list[float]]
    rearLimbs: list[list[float]]
    feetAnchor: list[float]
    groundAnchor: list[float]
    tailBoundingBox: list[float] | None
    tailPoint: list[float] | None
    facingDirection: str
    canonicalFacing: str
    confidence: float
    source: str
    userConfirmed: bool = False
    confirmedAt: str | None = None
    analysisVersion: str = ANALYSIS_VERSION

    def payload(self) -> dict:
        return asdict(self)


def _clamp(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _box(value: object, fallback: list[float]) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        return fallback
    left, top, width, height = (_clamp(item) for item in value)
    width = min(width, 1.0 - left)
    height = min(height, 1.0 - top)
    return [left, top, width, height] if width > 0.01 and height > 0.01 else fallback


def _point(value: object, fallback: list[float]) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return list(fallback)
    return [_clamp(value[0], fallback[0]), _clamp(value[1], fallback[1])]


def _optional_box(value: object, fallback: list[float] | None = None) -> list[float] | None:
    if value is None:
        return fallback
    base = fallback or [0.0, 0.0, 1.0, 1.0]
    return _box(value, base)


def _boxes(value: object) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    result: list[list[float]] = []
    for item in value:
        if isinstance(item, list) and len(item) == 4:
            result.append(_box(item, [0.0, 0.0, 1.0, 1.0]))
    return result


def _facing_sides(facing: str) -> tuple[str, str]:
    if facing == "LEFT":
        return "LEFT", "RIGHT"
    if facing == "RIGHT":
        return "RIGHT", "LEFT"
    return "FRONT", "BACK"


def _alpha_geometry(path: Path) -> CharacterGeometry:
    """Deterministic, offline geometry fallback over the transparent character.

    The upper third of a child drawing is a strong head candidate for the
    supported full-body drawings.  Comparing it with the lower-body centroid
    correctly identifies side-profile characters (including a head-left,
    tail-right dinosaur) without performing inference per slide or frame.
    """

    with Image.open(path) as source:
        rgba = np.asarray(source.convert("RGBA"))
    alpha = rgba[:, :, 3]
    ys, xs = np.where(alpha > 20)
    if len(xs) < 100:
        raise ValueError("Character image has no stable visible foreground")
    image_h, image_w = alpha.shape
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    bbox = [x1 / image_w, y1 / image_h, (x2 - x1) / image_w, (y2 - y1) / image_h]

    visible_h = max(1, y2 - y1)
    head_limit = y1 + max(1, round(visible_h * 0.42))
    head_y, head_x = np.where((alpha > 20) & (np.indices(alpha.shape)[0] <= head_limit))
    if len(head_x) < 30:
        head_x, head_y = xs, ys
    body_y, body_x = np.where((alpha > 20) & (np.indices(alpha.shape)[0] > head_limit))
    if len(body_x) < 30:
        body_x, body_y = xs, ys

    head_cx = float(head_x.mean() / image_w)
    head_cy = float(head_y.mean() / image_h)
    body_cx = float(body_x.mean() / image_w)
    body_cy = float(body_y.mean() / image_h)
    separation = (head_cx - body_cx) / max(bbox[2], 0.01)
    if separation <= -0.105:
        facing = "LEFT"
    elif separation >= 0.105:
        facing = "RIGHT"
    else:
        facing = "FRONT"
    confidence = min(0.9, 0.72 + abs(separation) * 0.55) if facing != "FRONT" else max(0.72, 0.86 - abs(separation))
    hx1, hx2 = int(head_x.min()), int(head_x.max()) + 1
    hy1, hy2 = int(head_y.min()), int(head_y.max()) + 1
    bx1, bx2 = int(body_x.min()), int(body_x.max()) + 1
    by1, by2 = int(body_y.min()), int(body_y.max()) + 1
    feet_mask = (alpha > 20) & (np.indices(alpha.shape)[0] >= y2 - max(2, round(visible_h * 0.08)))
    feet_y, feet_x = np.where(feet_mask)
    feet_cx = float((feet_x.mean() if len(feet_x) else (x1 + x2) / 2) / image_w)
    feet_y_norm = float(((feet_y.max() + 1) if len(feet_y) else y2) / image_h)
    front_side, back_side = _facing_sides(facing)
    front_x = x1 / image_w if facing == "LEFT" else x2 / image_w if facing == "RIGHT" else head_cx
    back_x = x2 / image_w if facing == "LEFT" else x1 / image_w if facing == "RIGHT" else body_cx
    tail_box = None
    if facing in {"LEFT", "RIGHT"}:
        if facing == "LEFT":
            tail_mask = (alpha > 20) & (np.indices(alpha.shape)[1] >= body_cx * image_w + bbox[2] * image_w * 0.12)
        else:
            tail_mask = (alpha > 20) & (np.indices(alpha.shape)[1] <= body_cx * image_w - bbox[2] * image_w * 0.12)
        tail_y, tail_x = np.where(tail_mask)
        if len(tail_x) >= 30:
            tx1, tx2 = int(tail_x.min()), int(tail_x.max()) + 1
            ty1, ty2 = int(tail_y.min()), int(tail_y.max()) + 1
            tail_box = [tx1 / image_w, ty1 / image_h, (tx2 - tx1) / image_w, (ty2 - ty1) / image_h]
    tail_point = [back_x, body_cy] if tail_box else None
    return CharacterGeometry(
        characterBoundingBox=[round(item, 6) for item in bbox],
        sourceWidth=image_w,
        sourceHeight=image_h,
        visibleAspectRatio=round((x2 - x1) / max(1, y2 - y1), 6),
        headCenterX=round(head_cx, 6),
        headCenterY=round(head_cy, 6),
        headPoint=[round(head_cx, 6), round(head_cy, 6)],
        headBoundingBox=[round(hx1 / image_w, 6), round(hy1 / image_h, 6), round((hx2 - hx1) / image_w, 6), round((hy2 - hy1) / image_h, 6)],
        bodyCenterX=round(body_cx, 6),
        bodyCenterY=round(body_cy, 6),
        torsoBoundingBox=[round(bx1 / image_w, 6), round(by1 / image_h, 6), round((bx2 - bx1) / image_w, 6), round((by2 - by1) / image_h, 6)],
        frontSide=front_side,
        backSide=back_side,
        frontPoint=[round(front_x, 6), round(head_cy, 6)],
        backPoint=[round(back_x, 6), round(body_cy, 6)],
        frontLimbs=[],
        rearLimbs=[],
        feetAnchor=[round(feet_cx, 6), round(feet_y_norm, 6)],
        groundAnchor=[round(feet_cx, 6), round(feet_y_norm, 6)],
        tailBoundingBox=[round(item, 6) for item in tail_box] if tail_box else None,
        tailPoint=[round(item, 6) for item in tail_point] if tail_point else None,
        facingDirection=facing,
        canonicalFacing=facing,
        confidence=round(confidence, 4),
        source="alpha_geometry",
    )


def _normalized_provider_geometry(data: dict, fallback: CharacterGeometry) -> CharacterGeometry:
    facing = str(data.get("facingDirection") or data.get("facing_direction") or "UNKNOWN").upper()
    if facing not in FACING_DIRECTIONS:
        facing = "UNKNOWN"
    bbox = _box(data.get("characterBoundingBox") or data.get("character_bounding_box"), fallback.characterBoundingBox)
    head_box_raw = data.get("headBoundingBox") or data.get("head_bounding_box")
    head_box = _box(head_box_raw, fallback.headBoundingBox or bbox) if head_box_raw else fallback.headBoundingBox
    facing_value = facing if facing != "UNKNOWN" else fallback.facingDirection
    front_side, back_side = _facing_sides(facing_value)
    feet_anchor = _point(data.get("feetAnchor") or data.get("feet_anchor"), fallback.feetAnchor)
    ground_anchor = _point(data.get("groundAnchor") or data.get("ground_anchor"), feet_anchor)
    source_width = max(1, int(data.get("sourceWidth") or fallback.sourceWidth))
    source_height = max(1, int(data.get("sourceHeight") or fallback.sourceHeight))
    visible_aspect = float(data.get("visibleAspectRatio") or ((source_width * bbox[2]) / max(1.0, source_height * bbox[3])))
    return CharacterGeometry(
        characterBoundingBox=bbox,
        sourceWidth=source_width,
        sourceHeight=source_height,
        visibleAspectRatio=round(max(0.05, min(8.0, visible_aspect)), 6),
        headCenterX=_clamp(data.get("headCenterX"), fallback.headCenterX),
        headCenterY=_clamp(data.get("headCenterY"), fallback.headCenterY),
        headPoint=_point(data.get("headPoint") or data.get("head_point"), fallback.headPoint),
        headBoundingBox=head_box,
        bodyCenterX=_clamp(data.get("bodyCenterX"), fallback.bodyCenterX),
        bodyCenterY=_clamp(data.get("bodyCenterY"), fallback.bodyCenterY),
        torsoBoundingBox=_box(data.get("torsoBoundingBox") or data.get("torso_bbox"), fallback.torsoBoundingBox),
        frontSide=str(data.get("frontSide") or front_side).upper(),
        backSide=str(data.get("backSide") or back_side).upper(),
        frontPoint=_point(data.get("frontPoint") or data.get("front_point"), fallback.frontPoint),
        backPoint=_point(data.get("backPoint") or data.get("back_point"), fallback.backPoint),
        frontLimbs=_boxes(data.get("frontLimbs") or data.get("front_limbs")) or fallback.frontLimbs,
        rearLimbs=_boxes(data.get("rearLimbs") or data.get("rear_limbs")) or fallback.rearLimbs,
        feetAnchor=feet_anchor,
        groundAnchor=ground_anchor,
        tailBoundingBox=_optional_box(data.get("tailBoundingBox") or data.get("tail_bbox"), fallback.tailBoundingBox),
        tailPoint=_point(data.get("tailPoint") or data.get("tail_point"), fallback.tailPoint or fallback.backPoint) if (data.get("tailPoint") or data.get("tail_point") or fallback.tailPoint) else None,
        facingDirection=facing_value,
        canonicalFacing=facing_value,
        confidence=_clamp(data.get("confidence"), 0.0),
        source="vision_verified",
    )


async def _vision_pass(path: Path, *, verification: bool = False) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    emphasis = (
        "This is a second verification pass. Pay special attention to which side contains the HEAD versus a tail, "
        "and do not infer facing from the direction the body merely leans. " if verification else ""
    )
    prompt = (
        emphasis
        + "Analyze exactly one transparent full-body child character. Return JSON only with: "
        "characterBoundingBox [left,top,width,height], headPoint, headBoundingBox, torsoBoundingBox, "
        "frontPoint, backPoint, feetAnchor, groundAnchor, optional tailBoundingBox and tailPoint, "
        "frontLimbs and rearLimbs as arrays of boxes, facingDirection LEFT/RIGHT/FRONT/UNKNOWN, confidence 0..1. "
        "All coordinates are normalized to the complete image. LEFT means the character's head/nose points left; "
        "RIGHT means it points right. A side-profile dinosaur with head on the left and tail on the right is LEFT."
    )
    payload = {
        "model": settings.openai_text_model or "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You inspect character geometry for deterministic animation placement."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"}},
            ]},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"character vision HTTP {response.status_code}")
    return json.loads(response.json()["choices"][0]["message"]["content"])


async def analyze_character_geometry(path: Path, *, allow_remote: bool = True) -> CharacterGeometry:
    """Analyze once at character creation/backfill; never during scene rendering."""

    fallback = _alpha_geometry(path)
    if not allow_remote or not settings.openai_api_key:
        return fallback
    try:
        first = _normalized_provider_geometry(await _vision_pass(path), fallback)
        if first.confidence >= 0.78 and first.facingDirection != "UNKNOWN":
            return first
        second = _normalized_provider_geometry(await _vision_pass(path, verification=True), fallback)
        if second.confidence >= first.confidence and second.facingDirection != "UNKNOWN":
            return second
        return first
    except Exception as exc:
        log.warning("Character vision analysis unavailable; using deterministic geometry: %s", exc)
        return fallback


def geometry_from_json(value: object) -> dict:
    try:
        payload = json.loads(str(value or "{}")) if not isinstance(value, dict) else dict(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def confirm_character_geometry(current: dict, submitted: dict) -> dict:
    """Merge bounded user marker corrections into the durable render contract."""

    if not current:
        raise ValueError("Character geometry is not available")
    result = dict(current)
    facing = str(submitted.get("facingDirection") or submitted.get("canonicalFacing") or current.get("facingDirection") or "UNKNOWN").upper()
    if facing not in FACING_DIRECTIONS - {"UNKNOWN"}:
        raise ValueError("facingDirection must be LEFT, RIGHT or FRONT")
    bbox = _box(current.get("characterBoundingBox"), [0.0, 0.0, 1.0, 1.0])
    head = _point(submitted.get("headPoint"), current.get("headPoint") or [current.get("headCenterX", 0.5), current.get("headCenterY", 0.25)])
    feet = _point(submitted.get("feetAnchor") or submitted.get("groundAnchor"), current.get("feetAnchor") or [0.5, bbox[1] + bbox[3]])
    front = _point(submitted.get("frontPoint"), current.get("frontPoint") or head)
    back = _point(submitted.get("backPoint"), current.get("backPoint") or [current.get("bodyCenterX", 0.5), current.get("bodyCenterY", 0.6)])
    result.update({
        "headPoint": head, "headCenterX": head[0], "headCenterY": head[1],
        "feetAnchor": feet, "groundAnchor": feet,
        "frontPoint": front, "backPoint": back,
        "facingDirection": facing, "canonicalFacing": facing,
        "frontSide": _facing_sides(facing)[0], "backSide": _facing_sides(facing)[1],
        "userConfirmed": True,
        "confirmedAt": datetime.now(timezone.utc).isoformat(),
        "analysisVersion": ANALYSIS_VERSION,
    })
    if "tailPoint" in submitted:
        result["tailPoint"] = _point(submitted.get("tailPoint"), current.get("tailPoint") or back)
    return result


def geometry_status(geometry: CharacterGeometry | dict) -> str:
    payload = geometry.payload() if isinstance(geometry, CharacterGeometry) else geometry
    if payload.get("userConfirmed") is True:
        return "CONFIRMED"
    confidence = _clamp(payload.get("confidence"), 0.0)
    facing = str(payload.get("facingDirection") or "UNKNOWN").upper()
    return "READY" if confidence >= 0.7 and facing in FACING_DIRECTIONS - {"UNKNOWN"} else "NEEDS_REVIEW"
