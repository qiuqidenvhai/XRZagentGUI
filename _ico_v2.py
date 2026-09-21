# -*- coding: utf-8 -*-
"""逐档识别 BMP/PNG 并解码，验证任务栏真实用的 16/32px BMP 档。"""
import struct, json, os, io
from PIL import Image

base = r'D:\软件\XianRenZhangAgent'
d = open(os.path.join(base, '__xianrenzhang_icon.ico'), 'rb').read()
_, _, count = struct.unpack_from('<HHH', d, 0)

out = []
off = 6
for i in range(count):
    w, h, colors, resv, planes, bpp, size, o = struct.unpack_from('<BBBBHHII', d, off)
    off += 16
    blob = d[o:o+size]
    is_png = blob[:8] == b'\x89PNG\r\n\x1a\n'
    if is_png:
        img = Image.open(io.BytesIO(blob)).convert('RGBA')
    else:
        biSize, biW, biH = struct.unpack_from('<Iii', blob, 0)
        W, H = biW, abs(biH)
        pix = blob[40:40+W*H*4]
        img = Image.new('RGBA', (W, H))
        px = []
        topdown = biH < 0
        for y in range(H):
            row = y if topdown else (H-1-y)
            for x in range(W):
                idx = (row*W + x)*4
                b,g,r,a = pix[idx], pix[idx+1], pix[idx+2], pix[idx+3]
                px.append((r,g,b,a))
        img.putdata(px)
    p = img.size
    px = list(img.getdata())
    total = len(px)
    opaque = sum(1 for c in px if c[3] > 128)
    green = sum(1 for c in px if c[3] > 128 and c[1] > c[0] and c[1] > c[2] and c[1] > 60)
    # 导出
    outp = os.path.join(base, f'_ico_v2_{p[0]}px_{"png" if is_png else "bmp"}.png')
    img.save(outp)
    out.append({'i': i, 'fmt': 'png' if is_png else 'bmp',
                'w': w, 'h': h, 'decoded': list(p),
                'opaque%': round(opaque/total*100, 1),
                'green%': round(green/total*100, 1),
                'preview': os.path.basename(outp)})
print(json.dumps(out, ensure_ascii=False, indent=1))
