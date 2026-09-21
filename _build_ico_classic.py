"""从 PNG 母图构建经典格式 Windows .ico（32bit DIB + AND 掩码）。

为什么不用 Pillow 的 save(format='ICO')：
  Pillow 会把 256x256 写成 PNG-inside-ICO，且部分档位用 PNG 压缩。
  Windows 任务栏/资源管理器在若干场景下对小尺寸 PNG-inside-ICO 渲染为空，
  这也是"图标是空的"这类投诉的常见根因。

本脚本每一档位都写经典 32bit BMP DIB：
  文件布局 = ICONDIR(6) + ICONDIRENTRY*N(16) + [BITMAPINFOHEADER(40) + BGRA(top-down) + AND掩码(1bit)]
  · height 字段写 2*h（DIB 惯例：颜色层 + 掩码层）
  · 像素 top-down（height 为负）
  · AND 掩码：alpha>0 处写 0（不透明/交给 alpha 通道），全部为 0 即可
"""

import os
import struct
import sys

from PIL import Image

APP = os.path.dirname(os.path.abspath(__file__))
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def _bmp_entry(img_rgba):
    """把一个 RGBA 图转成经典 ICO 里的 BMP 数据块。

    【关键坑】BITMAPINFOHEADER 的 biHeight 符号决定像素行序：
      · biHeight = +2h  → 行序为 bottom-up（DIB 默认），像素数据必须从下往上写
      · biHeight = -2h  → 行序为 top-down，像素数据从上往下写
    ICO 规范要求 biHeight = 2*h（颜色层 h + AND 掩码 h），**是正数**，
    所以像素必须按 bottom-up 写。之前用 top-down 写 + 正 biHeight 声明，
    结果 Windows 按 bottom-up 解读 → 图标整体上下翻转（仙人掌倒栽葱）。
    """
    w, h = img_rgba.size
    px = img_rgba.load()
    # BGRA, bottom-up（与正 biHeight 匹配）
    rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    color = b"".join(rows)
    # AND 掩码：每行按 4 字节对齐，1bit/px；全 0 = 全部由 alpha 决定
    mask_row_bytes = ((w + 31) // 32) * 4
    mask = b"\x00" * (mask_row_bytes * h)
    bih = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        w,           # biWidth
        h * 2,       # biHeight = 2*h（正数 → bottom-up 行序）
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        len(color) + len(mask),  # biSizeImage
        0, 0, 0, 0,
    )
    return bih + color + mask


def build(src_png, out_ico, sizes=None):
    sizes = sizes or SIZES
    src = Image.open(src_png).convert("RGBA")
    blobs = []
    for s in sizes:
        im = src.resize((s, s), Image.LANCZOS)
        blobs.append((s, _bmp_entry(im)))

    n = len(blobs)
    offset = 6 + 16 * n
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    body = b""
    for (s, data) in blobs:
        entries += struct.pack(
            "<BBBBHHII",
            s if s < 256 else 0,
            s if s < 256 else 0,
            0, 0, 1, 32,
            len(data), offset + len(body),
        )
        body += data
    with open(out_ico, "wb") as f:
        f.write(header + entries + body)
    print("wrote %s  sizes=%s  bytes=%d" % (out_ico, sizes, os.path.getsize(out_ico)))
    return out_ico


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP, "__xianrenzhang_icon_v3.png")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(APP, "__xianrenzhang_icon_v3.ico")
    build(src, out)
