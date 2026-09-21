"""统一重建两个图标，消除「只显示 1 秒 / 显示成丑图」的全部根因。

根因总结
--------
1. **浏览器（仙人球）**：正确图案是 `__browser_cactus_preview.png`
   （暗色浏览器圆环 + 圆形带棱仙人球 + 顶部小花）。但
   `__browser_cactus_icon.ico` 里装的却是 `make_cactus_browser_icon.py` 生成的
   另一版（竖棱是「细长椭圆」，看起来像一颗西瓜/丑球），而 `patch_chrome_icon.py`
   把这个丑 ico 写进了 chrome.exe 资源 → 任务栏显示丑图。
2. **控制面板（盆栽仙人掌）**：正确图案是 `__xianrenzhang_icon.png`（清晰盆栽仙人掌）。
   但 ICO 里 6 个 entry 全是 **PNG 压缩**；Windows 任务栏/标题栏只认经典
   32bit **BMP(DIB)** entry，PNG-only 的 ico 在小尺寸下会回退到通用图标 = 空白/坏图。

修复
----
- 所有 entry 写成经典 32-bit BMP(DIB) + 1-bit AND 掩码（Windows 100% 支持）。
- 浏览器 ico 严格从 `__browser_cactus_preview.png` 生成。
- 控制面板 ico 严格从 `__xianrenzhang_icon.png` 生成。
- 尺寸覆盖 Windows 实际使用的 16/20/24/32/40/48/64/96/128/256。
- 发布用新文件名（内容哈希）后拷回旧名，规避 Windows 图标缓存。
"""
import hashlib
import os
import shutil
import struct
import sys

from PIL import Image

BASE = r"D:/软件/XianRenZhangAgent"

# Windows 实际会用的尺寸（含 20/40 这类 Win10/11 常用小尺寸）
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def make_dib(img_rgba, size):
    """把 RGBA 图转成经典 32-bit BMP(DIB) icon entry 数据。

    结构: BITMAPINFOHEADER(40B, 高度=2*h) + BGRA 像素(bottom-up) + AND 掩码
    """
    im = img_rgba.convert("RGBA").resize((size, size), Image.LANCZOS)
    px = im.load()

    # BGRA，自下而上
    bgra = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = px[x, y]
            bgra += bytes((b, g, r, a))

    # 1-bit AND 掩码：每行按 4 字节对齐；alpha==0 → 置 1（透明）
    row_bytes = ((size + 31) // 32) * 4
    mask = bytearray()
    for y in range(size - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(size):
            if px[x, y][3] == 0:
                row[x >> 3] |= 0x80 >> (x & 7)
        mask += row

    header = struct.pack(
        "<IiiHHIIiiII",
        40,           # biSize
        size,         # biWidth
        size * 2,     # biHeight（图像+掩码）
        1,            # biPlanes
        32,           # biBitCount
        0,            # biCompression = BI_RGB
        len(bgra),    # biSizeImage
        0, 0, 0, 0,   # 分辨率 / 调色板
    )
    return bytes(header) + bytes(bgra) + bytes(mask)


def write_ico(png_path, out_path, sizes=SIZES):
    """从 PNG 生成经典 BMP-entry 多尺寸 ICO。"""
    src = Image.open(png_path).convert("RGBA")
    entries = []
    for s in sizes:
        entries.append((s, make_dib(src, s)))

    n = len(entries)
    header = struct.pack("<HHH", 0, 1, n)   # reserved=0, type=1(icon), count
    dir_tbl = b""
    blobs = b""
    offset = 6 + n * 16
    for s, data in entries:
        bw = 0 if s >= 256 else s
        bh = 0 if s >= 256 else s
        dir_tbl += struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)

    raw = header + dir_tbl + blobs
    with open(out_path, "wb") as f:
        f.write(raw)
    return raw, [s for s, _ in entries]


def cache_bust(path):
    """写新路径再拷回旧名，改 mtime/inode，绕过 Windows 图标缓存。"""
    tmp = path + ".new"
    shutil.copyfile(path, tmp)
    shutil.copyfile(tmp, path)
    try:
        os.remove(tmp)
    except OSError:
        pass


def main():
    jobs = [
        # (源 PNG, 目标 ICO, 别名列表)
        (os.path.join(BASE, "__browser_cactus_preview.png"),
         os.path.join(BASE, "__browser_cactus_icon.ico"), []),
        (os.path.join(BASE, "__xianrenzhang_icon.png"),
         os.path.join(BASE, "__xianrenzhang_icon.ico"),
         [os.path.join(BASE, "xianrenzhang_cactus.ico")]),
    ]

    for src_png, out_ico, aliases in jobs:
        if not os.path.exists(src_png):
            print(f"[FAIL] 源 PNG 不存在: {src_png}")
            return 1
        raw, done = write_ico(src_png, out_ico)
        print(f"[OK] {os.path.basename(out_ico):<32} "
              f"{len(raw):>7d}B  sizes={done}")
        print(f"     源: {os.path.basename(src_png)}  "
              f"md5={hashlib.md5(raw).hexdigest()[:12]}")
        cache_bust(out_ico)
        for a in aliases:
            shutil.copyfile(out_ico, a)
            cache_bust(a)
            print(f"     别名已同步: {os.path.basename(a)}")

    # 校验：确认每个 entry 真的是 DIB 而不是 PNG
    print("\n=== 校验 ===")
    for _src, ico, _a in jobs:
        d = open(ico, "rb").read()
        _rsv, _typ, cnt = struct.unpack("<HHH", d[:6])
        kinds = []
        for i in range(cnt):
            _bw, _bh, _c, _r, _p, _bpp, _sz, o = struct.unpack(
                "<BBBBHHII", d[6 + i * 16:6 + i * 16 + 16])
            kinds.append("DIB" if d[o:o + 4] == b"\x28\x00\x00\x00" else "PNG")
        ok = all(k == "DIB" for k in kinds)
        print(f"{os.path.basename(ico):<32} entries={cnt} kinds={set(kinds)} "
              f"{'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
