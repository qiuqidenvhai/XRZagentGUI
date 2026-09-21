#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forensic analysis: does the ICO's stored DIB match the source orientation?
Reconstruct the 256px entry two ways and compare to the source PNG.
"""
import os, struct
from PIL import Image

BASE = r'D:/软件/XianRenZhangAgent'

def load_dib_xor(ico_bytes, entry_offset, entry_size, size):
    """Read XOR BGRA scanlines starting at entry_offset (after 40-byte header)."""
    # skip BITMAPINFOHEADER (40 bytes)
    xor = ico_bytes[entry_offset+40: entry_offset+40 + size*size*4]
    return xor

def xor_to_img(xor, size, flip):
    """flip=True => treat stored rows as bottom-up (standard BMP); flip=False => top-down."""
    img = Image.new('RGBA', (size, size))
    px = img.load()
    for row in range(size):
        if flip:
            src_row = size - 1 - row   # bottom-up: file row 0 = image bottom
        else:
            src_row = row
        base = src_row * size * 4
        for x in range(size):
            b, g, r, a = xor[base + x*4: base + x*4 + 4]
            px[x, row] = (r, g, b, a)
    return img

def avg_brown_topbottom(img):
    """Return (top_brown_ratio, bottom_brown_ratio) to locate the pot."""
    w, h = img.size
    px = img.load()
    def brown_ratio(y0, y1):
        c = 0; n = 0
        for y in range(y0, y1):
            for x in range(0, w, 4):
                r, g, b, a = px[x, y]
                if a < 40: continue
                # brown pot: r>g>b, r in mid-high, low blue
                if r > 80 and r > b + 30 and g > b and r >= g and not (g > 180 and r > 180):
                    c += 1
                n += 1
        return c / max(n, 1)
    return brown_ratio(0, h//2), brown_ratio(h//2, h)

# source
src = Image.open(os.path.join(BASE, '__xianrenzhang_icon.png')).convert('RGBA')
print('SOURCE size', src.size, 'top/bottom brown', avg_brown_topbottom(src))
src.save(os.path.join(BASE, '_src_check.png'))

# ico
d = open(os.path.join(BASE, '__xianrenzhang_icon.ico'), 'rb').read()
reserved, type_, n = struct.unpack('<HHH', d[:6])
off = 6
target_off = None; target_size = None
for i in range(n):
    bw, bh, bc, rsv, planes, bpp, sz, o = struct.unpack('<BBBBHHII', d[off:off+16])
    w = bw or 256; h = bh or 256
    if w == 256:
        target_off = o; target_size = sz
    off += 16
print('ICO entries', n, '256 entry offset', target_off, 'size', target_size)
xor = load_dib_xor(d, target_off, target_size, 256)
img_bottomup = xor_to_img(xor, 256, flip=True)   # BMP standard
img_topdown   = xor_to_img(xor, 256, flip=False)  # as-is
print('ICO-as-BMP(bottom-up) top/bottom brown', avg_brown_topbottom(img_bottomup))
print('ICO-as-topdown        top/bottom brown', avg_brown_topbottom(img_topdown))
img_bottomup.save(os.path.join(BASE, '_ico_as_bmp.png'))
img_topdown.save(os.path.join(BASE, '_ico_as_topdown.png'))
