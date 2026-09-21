"""把「仙人球」图标写入 Playwright Chromium 的 chrome.exe 资源（替换 IDR_MAINFRAME）。

背景
----
Chromium 的标题栏是自绘的，任务栏图标取自「启动时 exe 里的图标资源」，
因此只发 WM_SETICON 改不掉任务栏/标题栏图标，必须改 exe 的图标资源。

关键修正（2026-09-21）
--------------------
之前本脚本把 `__browser_cactus_icon.ico` 的 PNG-compressed entry **原样**塞进
exe 的 RT_ICON，导致 explorer 在部分尺寸下回退到通用图标，表现为
「仙人球只闪 1 秒就变回旧图」。现在改为：

1. 先从 `.cactus_backup` 还原 exe（保证每次都在干净的官方资源上改）；
2. 把源 ICO 的每个 entry 统一转成经典 32-bit BMP(DIB) + AND 掩码再写入；
3. 同时写入 RT_GROUP_ICON(IDR_MAINFRAME) 与全部 RT_ICON。

用法：
    python patch_chrome_icon.py            # dry-run，只解析不写入
    python patch_chrome_icon.py --apply    # 实际写入
"""
import os
import shutil
import struct
import sys

import win32api
import win32con
from PIL import Image

ICON = r"D:\软件\XianRenZhangAgent\__browser_cactus_icon.ico"

CHROMES = [
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.exe",
]

GROUP_NAME = "IDR_MAINFRAME"
NEW_ICON_BASE = 100          # 新 RT_ICON 起始 id（避开原有 1~33）
LOAD_LIBRARY_AS_DATAFILE = 0x00000002

# 只改这些尺寸：过大的 256 会让 exe 膨胀且没必要，128 已足够任务栏/Alt-Tab 用
PATCH_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def make_dib(img_rgba, size):
    """RGBA -> 经典 32-bit BMP(DIB) icon entry（header + BGRA 自下而上 + AND 掩码）。"""
    im = img_rgba.convert("RGBA").resize((size, size), Image.LANCZOS)
    px = im.load()

    bgra = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = px[x, y]
            bgra += bytes((b, g, r, a))

    row_bytes = ((size + 31) // 32) * 4
    mask = bytearray()
    for y in range(size - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(size):
            if px[x, y][3] == 0:
                row[x >> 3] |= 0x80 >> (x & 7)
        mask += row

    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(bgra), 0, 0, 0, 0)
    return bytes(header) + bytes(bgra) + bytes(mask)


def load_icon_images(ico_path):
    """读 ICO，按 PATCH_SIZES 重建为 DIB entry 列表 [(w,h,bpp,data), ...]。"""
    im = Image.open(ico_path).convert("RGBA")
    out = []
    for s in PATCH_SIZES:
        out.append((s, s, 32, make_dib(im, s)))
    return out


def build_group(images, base_id):
    """构造 RT_GROUP_ICON 数据（GRPICONDIR + 每条 14 字节。nId 是 WORD，不是 4 字节偏移）。"""
    buf = struct.pack("<HHH", 0, 1, len(images))
    for i, (w, h, bitcount, data) in enumerate(images):
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        buf += struct.pack("<BBBBHHIH", bw, bh, 0, 0, 1, bitcount,
                           len(data), base_id + i)
    return buf


def restore_from_backup(chrome):
    """从备份还原 exe，确保每次改动都基于原始官方资源。"""
    bak = chrome + ".cactus_backup"
    if not os.path.exists(bak):
        shutil.copy2(chrome, bak)
        print(f"  首次备份 -> {os.path.basename(bak)}")
        return False
    shutil.copy2(bak, chrome)
    print(f"  已从备份还原（干净基线）")
    return True


def main(apply=False):
    images = load_icon_images(ICON)
    print(f"仙人球图标: {len(images)} 个尺寸 -> {[(w, h) for w, h, _, _ in images]}")
    print(f"  全部为经典 DIB entry: "
          f"{all(d[:4] == b'\x28\x00\x00\x00' for _, _, _, d in images)}")
    group_data = build_group(images, NEW_ICON_BASE)
    print(f"  GRPICONDIR: {len(group_data)}B (应为 6+{len(images)}*14="
          f"{6 + len(images) * 14})")

    for chrome in CHROMES:
        print("\n" + "=" * 66)
        print("目标:", chrome)
        if not os.path.exists(chrome):
            print("  [跳过] 文件不存在")
            continue

        if not apply:
            h = win32api.LoadLibraryEx(chrome, 0, LOAD_LIBRARY_AS_DATAFILE)
            try:
                langs = win32api.EnumResourceLanguages(h, win32con.RT_GROUP_ICON, GROUP_NAME)
                have = win32api.EnumResourceNames(h, win32con.RT_ICON)
            finally:
                win32api.FreeLibrary(h)
            print(f"  原 {GROUP_NAME} 语言={langs[0] if langs else 0}  "
                  f"现有 RT_ICON 数={len(have)}")
            print("  [dry-run] 未写入。加 --apply 实际执行。")
            continue

        restore_from_backup(chrome)

        h = win32api.LoadLibraryEx(chrome, 0, LOAD_LIBRARY_AS_DATAFILE)
        try:
            langs = win32api.EnumResourceLanguages(h, win32con.RT_GROUP_ICON, GROUP_NAME)
        finally:
            win32api.FreeLibrary(h)
        lang = langs[0] if langs else 0
        print(f"  写入语言={lang}")

        hu = win32api.BeginUpdateResource(chrome, 0)
        try:
            for i, (_w, _h, _bpp, data) in enumerate(images):
                win32api.UpdateResource(hu, win32con.RT_ICON,
                                        NEW_ICON_BASE + i, data, lang)
            win32api.UpdateResource(hu, win32con.RT_GROUP_ICON,
                                    GROUP_NAME, group_data, lang)
            win32api.EndUpdateResource(hu, 0)
        except Exception as e:
            win32api.EndUpdateResource(hu, True)
            print(f"  [失败] {e}")
            continue
        print(f"  已写入仙人球图标 OK  ({os.path.getsize(chrome)} bytes)")


if __name__ == "__main__":
    main(apply=("--apply" in sys.argv))
