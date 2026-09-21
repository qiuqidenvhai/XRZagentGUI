# -*- coding: utf-8 -*-
"""把 ui_dumps 里的网页交互截图合成 PDF，便于直接翻看「网页上到底发生了什么」。

用法:
    python _dump_to_pdf.py                 # 全部平台，各自合成一份 PDF
    python _dump_to_pdf.py doubao          # 只合成指定平台
    python _dump_to_pdf.py doubao 20       # 只取最近 20 张

有头 Chromium 不支持 Playwright 的 page.pdf()，所以运行时存的是 PNG，
这里用 Pillow 把同一次回归的截图按时间顺序拼成 PDF。
"""
import sys, os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\ui_dumps")
OUT = Path(r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\ui_dumps_pdf")


def build(platform: str, limit: int = 0):
    d = BASE / platform
    if not d.is_dir():
        print(f"  跳过 {platform}：无截图目录")
        return None
    files = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime)
    if limit:
        files = files[-limit:]
    if not files:
        print(f"  跳过 {platform}：没有截图")
        return None
    try:
        from PIL import Image
    except ImportError:
        print("  需要 Pillow：pip install Pillow")
        return None
    imgs = []
    for f in files:
        try:
            im = Image.open(f)
            if im.mode != "RGB":
                im = im.convert("RGB")
            # 超长整页截图按比例缩到宽度 1000，避免 PDF 巨大
            w, h = im.size
            if w > 1000:
                im = im.resize((1000, int(h * 1000 / w)), Image.LANCZOS)
            imgs.append((f.name, im))
        except Exception as e:
            print(f"  跳过损坏图片 {f.name}: {e}")
    if not imgs:
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{platform}_interaction.pdf"
    first = imgs[0][1]
    rest = [im for _, im in imgs[1:]]
    first.save(out, save_all=True, append_images=rest)
    print(f"  ✅ {out}  （{len(imgs)} 页，{out.stat().st_size // 1024} KB）")
    for n, _ in imgs[:3]:
        print(f"      首几张: {n}")
    return out


if __name__ == "__main__":
    if not BASE.is_dir():
        print("还没有任何截图（需要 XRZ_UI_DUMP=1 跑一次）")
        sys.exit(0)
    plats = [sys.argv[1]] if len(sys.argv) > 1 else sorted(p.name for p in BASE.iterdir() if p.is_dir())
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    outs = []
    for p in plats:
        print(f"平台 {p}:")
        o = build(p, lim)
        if o:
            outs.append(o)
    print("\n生成:", *[str(o) for o in outs], sep="\n  ")
