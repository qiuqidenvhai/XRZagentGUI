#!/usr/bin/env python3
"""
Generate a valid Windows .ico from the cactus PNG (multi-resolution, with alpha).

History: the previous version of this file generated a "green gradient square"
from scratch and never read the cactus PNG — that produced an icon that Qt
could not even decode, so the taskbar fell back to a generic window icon.
This version reads the real cactus PNG and writes a proper multi-size ICO.
"""
from PIL import Image
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "__xianrenzhang_icon.png")
DST = os.path.join(HERE, "__xianrenzhang_icon.ico")

# Modern Windows picks the largest frame it can render; we include the sizes
# Windows actually uses for title bar / taskbar / alt-tab so each looks crisp.
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def main():
    if not os.path.exists(SRC):
        print(f"ERROR: source PNG missing: {SRC}")
        sys.exit(1)
    img = Image.open(SRC)
    # Keep alpha (RGBA) so the transparent background of the cactus survives.
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img.save(DST, format="ICO", sizes=SIZES)
    sz = os.path.getsize(DST)
    print(f"ICO written: {DST} ({sz} bytes), sizes={SIZES}")


if __name__ == "__main__":
    main()