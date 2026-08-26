from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from app.core.config import settings


log = logging.getLogger("dome.character_geometry")
ANALYSIS_VERSION = "character-geometry-v1"
FACING_DIRECTIONS = {"LEFT", "RIGHT", "FRONT", "UNKNOWN"}


@dataclass(frozen=True)
class CharacterGeometry:
    characterBoundingBox: list[float]
    headCenterX: float
    headCenterY: float
    headBoundingBox: list[float] | None
    bodyCenterX: float
    bodyCenterY: float
    facingDirection: str
    confidence: float
    source: str
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
    return CharacterGeometry(
        characterBoundingBox=[round(item, 6) for item in bbox],
        headCenterX=round(head_cx, 6),
        headCenterY=round(head_cy, 6),
        headBoundingBox=[round(hx1 / image_w, 6), round(hy1 / image_h, 6), round((hx2 - hx1) / image_w, 6), round((hy2 - hy1) / image_h, 6)],
        bodyCenterX=round(body_cx, 6),
        bodyCenterY=round(body_cy, 6),
        facingDirection=facing,
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
    return CharacterGeometry(
        characterBoundingBox=bbox,
        headCenterX=_clamp(data.get("headCenterX"), fallback.headCenterX),
        headCenterY=_clamp(data.get("headCenterY"), fallback.headCenterY),
        headBoundingBox=head_box,
        bodyCenterX=_clamp(data.get("bodyCenterX"), fallback.bodyCenterX),
        bodyCenterY=_clamp(data.get("bodyCenterY"), fallback.bodyCenterY),
        facingDirection=facing,
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
        "characterBoundingBox [left,top,width,height], headCenterX, headCenterY, optional headBoundingBox, "
        "bodyCenterX, bodyCenterY, facingDirection LEFT/RIGHT/FRONT/UNKNOWN, confidence 0..1. "
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


def geometry_status(geometry: CharacterGeometry | dict) -> str:
    payload = geometry.payload() if isinstance(geometry, CharacterGeometry) else geometry
    confidence = _clamp(payload.get("confidence"), 0.0)
    facing = str(payload.get("facingDirection") or "UNKNOWN").upper()
    return "READY" if confidence >= 0.7 and facing in FACING_DIRECTIONS - {"UNKNOWN"} else "NEEDS_REVIEW"
