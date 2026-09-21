#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a proper classic-DIB (BMP) multi-resolution .ico from a clean source PNG.
Avoids the PNG-inside-ICO taskbar-blank bug on Windows.
"""
import struct, os
from PIL import Image

BASE = r'D:/软件/XianRenZhangAgent'
SRC = os.path.join(BASE, '__xianrenzhang_icon.png')   # clear potted cactus (256x256 RGBA)
SIZES = [16, 32, 48, 64, 128, 256]


def build_dib(im, size):
    """Return (dib_bytes, xor_bytes, and_bytes) for one size, classic 32bpp BGRA DIB."""
    img = im.resize((size, size), Image.LANCZOS).convert('RGBA')
    px = img.load()
    # XOR block: BGRA, stored BOTTOM-UP (classic BMP with biHeight=+2*h)
    xor = bytearray()
    and_rows = []
    for y in range(size):
        row_bgra = bytearray()
        and_bits = []
        for x in range(size):
            r, g, b, a = px[x, y]
            row_bgra += bytes((b, g, r, a))
            and_bits.append(1 if a < 128 else 0)   # 1 = transparent in AND mask
        # store top-down scanlines in memory first; bottom-up is applied below
        xor = row_bgra + xor
        and_rows.append(and_bits)
    # AND mask: 1bpp, padded to 4-byte boundary per row, stored BOTTOM-UP
    row_bytes = (size + 31) // 32 * 4
    and_block = bytearray()
    for y in range(size - 1, -1, -1):
        bits = and_rows[y]
        packed = 0
        out = bytearray()
        for x in range(size):
            packed = (packed << 1) | bits[x]
            if (x + 1) % 8 == 0:
                out.append(packed)
                packed = 0
        if size % 8 != 0:
            out.append(packed << (8 - (size % 8)))
        # pad row to row_bytes
        out += b'\x00' * (row_bytes - len(out))
        and_block += out
    xor = bytes(xor)
    and_block = bytes(and_block)
    xor_size = size * size * 4
    and_size = row_bytes * size
    # BITMAPINFOHEADER
    bi = struct.pack('<IiiHHIIiiII',
                     40,        # biSize
                     size,      # biWidth
                     size * 2,  # biHeight (XOR+AND)
                     1,         # biPlanes
                     32,        # biBitCount
                     0,         # biCompression (BI_RGB)
                     xor_size + and_size,  # biSizeImage (approx)
                     0, 0, 0, 0)
    dib = bi + xor + and_block
    return dib


def main():
    src = Image.open(SRC).convert('RGBA')
    print('SRC', src.size, src.mode)
    entries = []  # (w,h,bpp,dib_bytes)
    for s in SIZES:
        dib = build_dib(src, s)
        entries.append((s, s, 32, dib))
    # assemble ICO
    icondir = struct.pack('<HHH', 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dir_entries = b''
    body = b''
    for (w, h, bpp, dib) in entries:
        sz = len(dib)
        # w/h of 256 stored as 0 in directory
        bw = w if w < 256 else 0
        bh = h if h < 256 else 0
        dir_entries += struct.pack('<BBBBHHII', bw, bh, 0, 0, 1, bpp, sz, offset)
        body += dib
        offset += sz
    ico = icondir + dir_entries + body
    targets = [
        '__xianrenzhang_icon.ico',
        '__browser_cactus_icon.ico',
        'xianrenzhang_cactus.ico',
    ]
    for t in targets:
        p = os.path.join(BASE, t)
        with open(p, 'wb') as f:
            f.write(ico)
        print('WROTE', t, len(ico))
    # verify by reading back header
    with open(os.path.join(BASE, '__xianrenzhang_icon.ico'), 'rb') as f:
        d = f.read()
    reserved, type_, n = struct.unpack('<HHH', d[:6])
    print('VERIFY reserved', reserved, 'type', type_, 'count', n)
    off = 6
    for i in range(n):
        bw, bh, bc, rsv, planes, bpp, sz, o = struct.unpack('<BBBBHHII', d[off:off+16])
        w = bw or 256; h = bh or 256
        magic = d[o:o+4]
        print(f'  entry{i}: {w}x{h} bpp={bpp} png={magic==b"\x89PNG"}')
        off += 16


if __name__ == '__main__':
    main()
