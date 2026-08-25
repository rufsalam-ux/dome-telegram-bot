from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BRANDING = ROOT / "assets" / "branding"
SOURCE = BRANDING / "dome-source-v2.jpg"
ICON = BRANDING / "dome-app-icon-v2.png"
ADAPTIVE = BRANDING / "dome-adaptive-foreground-v2.png"
SPLASH = BRANDING / "dome-splash-v2.png"
BLUE = (0, 125, 253, 255)


def remove_edge_matte(source: Image.Image) -> Image.Image:
    """Remove only the near-black JPEG matte connected to the outer edge."""

    rgba = source.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()

    def dark(x: int, y: int) -> bool:
        red, green, blue, _ = pixels[x, y]
        return max(red, green, blue) < 58

    for x in range(width):
        for y in (0, height - 1):
            if dark(x, y):
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if dark(x, y):
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not dark(x, y):
            continue
        visited.add((x, y))
        pixels[x, y] = (*pixels[x, y][:3], 0)
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= next_x < width and 0 <= next_y < height:
                queue.append((next_x, next_y))
    return rgba


def main() -> None:
    source = Image.open(SOURCE)
    cleaned = remove_edge_matte(source).resize((1024, 1024), Image.Resampling.LANCZOS)

    # Apple icons cannot contain alpha. The blue fill is hidden by the native
    # rounded mask while avoiding a black JPEG matte at the corners.
    icon = Image.new("RGBA", cleaned.size, BLUE)
    icon.alpha_composite(cleaned)
    icon.convert("RGB").save(ICON, optimize=True)

    # Splash keeps the exact supplied artwork and transparent outer corners.
    cleaned.save(SPLASH, optimize=True)

    # Android adaptive icons need generous safe-zone padding for round/squircle
    # launchers. The supplied artwork remains unchanged inside that safe zone.
    foreground = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    safe = cleaned.resize((780, 780), Image.Resampling.LANCZOS)
    foreground.alpha_composite(safe, ((1024 - 780) // 2, (1024 - 780) // 2))
    foreground.save(ADAPTIVE, optimize=True)


if __name__ == "__main__":
    main()
