# -*- coding: utf-8 -*-
"""硬验证 ico：用 PIL 解出 16/32/48px 档，统计非透明/绿色像素占比，导出预览。
若解出来是白屏/花屏 → 图标数据本身坏了。"""
import io, json, os
from PIL import Image

base = r'D:\软件\XianRenZhangAgent'
ico = os.path.join(base, '__xianrenzhang_icon.ico')

out = {}
im = Image.open(ico)
out['format'] = im.format
out['size_default'] = im.size
out['n_frames'] = getattr(im, 'n_frames', 1)

# 逐个可用尺寸导出 + 统计
avail = []
try:
    im.seek(0)
except Exception:
    pass
cur = 0
seen = set()
while True:
    try:
        im.seek(cur)
    except EOFError:
        break
    except Exception:
        break
    sz = im.size
    if sz in seen:
        cur += 1
        continue
    seen.add(sz)
    cur += 1
    rgba = im.convert('RGBA')
    px = list(rgba.getdata())
    total = len(px)
    opaque = sum(1 for p in px if p[3] > 128)
    green = sum(1 for p in px if p[3] > 128 and p[1] > p[0] and p[1] > p[2] and p[1] > 80)
    # 导出预览
    png = os.path.join(base, f'_ico_preview_{sz[0]}.png')
    rgba.save(png)
    avail.append({'size': sz, 'opaque%': round(opaque / total * 100, 1),
                  'green%': round(green / total * 100, 1),
                  'preview': os.path.basename(png)})

out['sizes'] = avail
print(json.dumps(out, ensure_ascii=False, indent=1))
