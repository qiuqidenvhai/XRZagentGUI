# -*- coding: utf-8 -*-
"""手动按 DIB 字节解码 ico 各档，存 PNG + 统计绿占比。PIL 解不动 32bpp 多档。"""
import struct, json, os
from PIL import Image

base = r'D:\软件\XianRenZhangAgent'
d = open(os.path.join(base, '__xianrenzhang_icon.ico'), 'rb').read()
reserved, ico_type, count = struct.unpack_from('<HHH', d, 0)

out = []
off = 6
for i in range(count):
    w, h, colors, resv, planes, bpp, size, o = struct.unpack_from('<BBBBHHII', d, off)
    off += 16
    # DIB 头 40 字节
    biSize, biW, biH, biPlanes, biBitCount = struct.unpack_from('<IiiHH', d, o)
    topdown = biH < 0
    W = biW
    H = abs(biH)
    pix_start = o + 40
    # 32bpp BGRA, top-down (biH<0)
    buf = d[pix_start:pix_start + W * H * 4]
    img = Image.new('RGBA', (W, H))
    px = []
    for y in range(H):
        row = y if topdown else (H - 1 - y)
        base_idx = row * W * 4
        for x in range(W):
            idx = base_idx + x * 4
            b, g, r, a = buf[idx], buf[idx+1], buf[idx+2], buf[idx+3]
            px.append((r, g, b, a))
    img.putdata(px)
    png = os.path.join(base, f'_ico_manual_{W}x{H}.png')
    img.save(png)
    data = px
    total = len(data)
    opaque = sum(1 for p in data if p[3] > 128)
    green = sum(1 for p in data if p[3] > 128 and p[1] > p[0] and p[1] > p[2] and p[1] > 60)
    out.append({'i': i, 'W': W, 'H': H, 'bpp': biBitCount, 'topdown': topdown,
                'opaque%': round(opaque/total*100,1), 'green%': round(green/total*100,1),
                'preview': os.path.basename(png)})
print(json.dumps(out, ensure_ascii=False, indent=1))
