from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps

class CharacterProcessingError(RuntimeError):
    pass

def process_character(original_path: Path, output_path: Path) -> Path:
    """Remove a light paper background and save a transparent PNG."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(original_path) as src:
        src = ImageOps.exif_transpose(src).convert("RGB")
        src.thumbnail((2400, 2400))
        rgb = np.array(src)

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l = lab[:, :, 0]
    a = lab[:, :, 1]
    b = lab[:, :, 2]
    # White/grey paper tends to be bright and low-chroma.
    chroma = np.abs(a.astype(np.int16) - 128) + np.abs(b.astype(np.int16) - 128)
    background = (l > 205) & (chroma < 34)
    alpha = np.where(background, 0, 255).astype(np.uint8)
    alpha = cv2.medianBlur(alpha, 5)
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, kernel)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel)

    ys, xs = np.where(alpha > 20)
    if len(xs) < 100:
        raise CharacterProcessingError("Не удалось уверенно найти рисунок на листе.")
    pad = 30
    x1, x2 = max(0, xs.min()-pad), min(rgb.shape[1], xs.max()+pad+1)
    y1, y2 = max(0, ys.min()-pad), min(rgb.shape[0], ys.max()+pad+1)
    rgba = np.dstack([rgb, alpha])[y1:y2, x1:x2]
    Image.fromarray(rgba, "RGBA").save(output_path)
    return output_path
