# -*- coding: utf-8 -*-
"""Generate rounded app icons from the official WeRead source image.

Reads  : assets/icon_src.png  (official weread.qq.com apple-touch-icon, 152px)
Writes : assets/icon.png      (256px, rounded corners, RGBA — used by the tray)
         assets/icon.ico      (multi-size, rounded corners — window/exe icon)

Re-download the source image with:

    curl -sL -o assets/icon_src.png "https://rescdn.qqmail.com/node/wr/wrpage/style/images/independent/appleTouchIcon/apple-touch-icon-152x152.png"
"""
from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "assets" / "icon_src.png"
OUT_PNG = BASE / "assets" / "icon.png"
OUT_ICO = BASE / "assets" / "icon.ico"

TARGET = 256  # output size
RADIUS_RATIO = 0.22  # corner radius, iOS-style app icon proportion


def rounded(img: Image.Image, size: int, radius: int) -> Image.Image:
    """Fit `img` into a `size`x`size` square with transparent rounded corners."""
    img = img.convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    fitted = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    canvas.paste(fitted, (0, 0), mask)
    return canvas


radius = round(TARGET * RADIUS_RATIO)
big = rounded(Image.open(SRC), TARGET, radius)
big.save(OUT_PNG)
big.save(
    OUT_ICO,
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("saved:", OUT_PNG)
print("saved:", OUT_ICO)
