from __future__ import annotations

import hashlib
from pathlib import Path
from PIL import Image
from app.services.visual_localization import localize_embedded_russian_image


def _contain_size(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    scale = min(max_w / max(src_w, 1), max_h / max(src_h, 1))
    return max(1, round(src_w * scale)), max(1, round(src_h * scale))


async def render_slide(
    source: Path,
    output_root: Path,
    *,
    character_path: Path | None = None,
    character_box: list[float] | list[int] | None = None,
    target_language: str = "ru",
    localize_text: bool = True,
) -> Path:
    """Return the original slide, optionally composited with the child's hero.

    No translations, white boxes, captions or other text are ever drawn on the slide.
    character_box accepts normalized [x, y, width, height] values (0..1) or pixels.
    """
    if localize_text and target_language != "ru":
        source = await localize_embedded_russian_image(source, output_root, target_language)
    if not character_path or not character_box or not Path(character_path).exists():
        return source
    key = hashlib.sha256(
        f"{source}:{source.stat().st_mtime}:{character_path}:{Path(character_path).stat().st_mtime}:{character_box}".encode()
    ).hexdigest()[:24]
    out = output_root / "character-composite" / f"{source.stem}_{key}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    base = Image.open(source).convert("RGBA")
    hero = Image.open(character_path).convert("RGBA")
    bw, bh = base.size
    x, y, w, h = character_box
    if all(isinstance(v, (int, float)) and 0 <= float(v) <= 1 for v in character_box):
        x, y, w, h = int(float(x) * bw), int(float(y) * bh), int(float(w) * bw), int(float(h) * bh)
    else:
        x, y, w, h = map(int, (x, y, w, h))
    nw, nh = _contain_size(hero.width, hero.height, max(1, w), max(1, h))
    hero = hero.resize((nw, nh), Image.Resampling.LANCZOS)
    # Anchor hero to the bottom-center of its reserved box.
    px = x + (w - nw) // 2
    py = y + h - nh
    base.alpha_composite(hero, (px, py))
    base.convert("RGB").save(out, quality=95)
    return out


# Backward-compatible alias. It deliberately ignores overlay text.
async def render_localized_slide(source: Path, output_root: Path, target_language: str, overlay_text: str, box=None) -> Path:
    return await render_slide(source, output_root, target_language=target_language, localize_text=True)
