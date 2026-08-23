"""Deterministically extract transparent suitcase objects from the authored slide.

This tool never generates or redraws pixels. It crops the exact lesson artwork,
uses GrabCut only to derive alpha, and keeps the original RGB pixels untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


OBJECTS = {
    "jacket": ((165, 55, 455, 340), (35, 25, 255, 255)),
    "binoculars": ((480, 55, 780, 310), (20, 25, 270, 220)),
    "water": ((835, 45, 1075, 315), (35, 10, 200, 250)),
    "compass": ((1095, 65, 1385, 340), (25, 20, 260, 245)),
    "teddy": ((165, 350, 455, 650), (25, 20, 260, 275)),
    "camera": ((1060, 350, 1375, 630), (35, 30, 280, 230)),
    "telescope": ((180, 660, 470, 940), (30, 35, 260, 225)),
    "fish": ((475, 690, 795, 955), (25, 35, 285, 210)),
    "notebook": ((785, 665, 1055, 965), (25, 25, 235, 260)),
    "sunglasses": ((1035, 690, 1375, 950), (30, 45, 300, 185)),
}
SUITCASE_CROP = (445, 285, 1100, 685)


def _transparent_crop(source: np.ndarray, crop_box: tuple[int, int, int, int], object_box: tuple[int, int, int, int]) -> np.ndarray:
    left, top, right, bottom = crop_box
    crop = source[top:bottom, left:right].copy()
    height, width = crop.shape[:2]
    ox, oy, ow, oh = object_box
    mask = np.full((height, width), cv2.GC_BGD, np.uint8)
    margin = max(4, min(width, height) // 40)
    mask[margin:height-margin, margin:width-margin] = cv2.GC_PR_BGD
    mask[oy:oy+oh, ox:ox+ow] = cv2.GC_PR_FGD
    # Dark authored outlines are reliable foreground seeds. Unlike a rectangular
    # center seed they never teach GrabCut that the pastel card behind the item
    # is part of the object.
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    seed = (gray < 105)
    region = np.zeros_like(seed)
    region[oy:oy+oh, ox:ox+ow] = True
    mask[seed & region] = cv2.GC_FGD
    background = np.zeros((1, 65), np.float64)
    foreground = np.zeros((1, 65), np.float64)
    cv2.grabCut(crop, mask, None, background, foreground, 8, cv2.GC_INIT_WITH_MASK)
    alpha = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    light_background = ((hsv[:, :, 2] > 180) & (hsv[:, :, 1] < 125)).astype(np.uint8)
    count, labels = cv2.connectedComponents(light_background)
    border_labels = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    for label in border_labels:
        if label:
            alpha[labels == label] = 0
    # Remove detached decorations/card fragments while retaining every component
    # connected to at least one authored dark outline seed.
    count, labels = cv2.connectedComponents((alpha > 0).astype(np.uint8))
    keep = {int(label) for label in np.unique(labels[seed & region]) if label}
    alpha[~np.isin(labels, list(keep))] = 0
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    rgba = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha
    points = cv2.findNonZero((alpha > 24).astype(np.uint8))
    if points is None:
        raise RuntimeError(f"No foreground detected for crop {crop_box}")
    x, y, w, h = cv2.boundingRect(points)
    pad = 8
    x1, y1 = max(0, x-pad), max(0, y-pad)
    x2, y2 = min(width, x+w+pad), min(height, y+h+pad)
    return rgba[y1:y2, x1:x2]


def extract(source_path: Path, output_dir: Path) -> None:
    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if source is None:
        raise FileNotFoundError(source_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, (crop_box, object_box) in OBJECTS.items():
        result = _transparent_crop(source, crop_box, object_box)
        if not cv2.imwrite(str(output_dir / f"{name}.png"), result):
            raise RuntimeError(f"Could not write {name}.png")
    left, top, right, bottom = SUITCASE_CROP
    target = source[top:bottom, left:right]
    if not cv2.imwrite(str(output_dir / "suitcase-target.png"), target):
        raise RuntimeError("Could not write suitcase-target.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    extract(args.source, args.output_dir)


if __name__ == "__main__":
    main()
