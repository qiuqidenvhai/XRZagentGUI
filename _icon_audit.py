"""审计 chrome.exe 主图标资源：当前 vs 备份。

回答两个问题：
1) 当前 exe 里 IDR_MAINFRAME 组图标到底是什么（仙人球 / 盆栽仙人掌 / Chromium 原版）
2) 备份里是什么（应该是 Chromium 原版）

方法：用 LoadLibraryEx(LOAD_LIBRARY_AS_DATAFILE) 读资源，把 RT_GROUP_ICON 里的每个
RT_ICON 抽出来拼成 .ico，再用 PIL 渲染成 PNG 供人眼核对。
"""
import os
import struct
import sys

import win32api
import win32con
from PIL import Image

LOAD_LIBRARY_AS_DATAFILE = 0x00000002
GROUP_NAME = "IDR_MAINFRAME"

BASE = r"D:/软件/XianRenZhangAgent"
CHROMES = [
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.exe",
]


def extract_group_icons(exe):
    """返回 list of (size, bytes) —— 每个尺寸一个完整 ico 数据块（PNG 或 DIB）。"""
    h = win32api.LoadLibraryEx(exe, 0, LOAD_LIBRARY_AS_DATAFILE)
    try:
        try:
            langs = win32api.EnumResourceLanguages(h, win32con.RT_GROUP_ICON, GROUP_NAME)
        except Exception as e:
            return None, f"no group icon {GROUP_NAME}: {e}"
        if not langs:
            return None, f"{GROUP_NAME} not found"
        lang = langs[0]
        grp = win32api.LoadResource(h, win32con.RT_GROUP_ICON, GROUP_NAME)
        # GRPICONDIR: WORD reserved, WORD type, WORD count
        _rsv, _typ, cnt = struct.unpack("<HHH", grp[:6])
        out = []
        for i in range(cnt):
            off = 6 + i * 14
            (bw, bh, ccount, rsv, planes, bpp, bytes_in_res, nid) = struct.unpack(
                "<BBBBHHIH", grp[off:off + 14]
            )
            data = win32api.LoadResource(h, win32con.RT_ICON, nid)
            w = bw or 256
            hh = bh or 256
            out.append((w, hh, bpp, bytes(data)))
        return out, None
    finally:
        win32api.FreeLibrary(h)


def build_ico(images):
    """把资源数据拼成一个标准 .ico，方便 PIL 打开。"""
    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    blobs = b""
    offset = 6 + n * 16
    for (w, hh, bpp, data) in images:
        bw = 0 if w >= 256 else w
        bh = 0 if hh >= 256 else hh
        entries += struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, bpp, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main():
    for exe in CHROMES:
        for label, path in (("CURRENT", exe), ("BACKUP", exe + ".cactus_backup")):
            print("=" * 70)
            print(f"{os.path.basename(os.path.dirname(os.path.dirname(exe)))} [{label}]")
            print(path)
            if not os.path.exists(path):
                print("  MISSING")
                continue
            imgs, err = extract_group_icons(path)
            if err:
                print("  ERR", err)
                continue
            print(f"  group '{GROUP_NAME}': {len(imgs)} sizes")
            for (w, hh, bpp, data) in imgs:
                kind = "PNG" if data[:4] == b"\x89PNG" else (
                    "DIB" if data[:4] == b"\x28\x00\x00\x00" else "UNK")
                print(f"    {w:>3}x{hh:<3} bpp={bpp} {len(data):>7d}B {kind}")
            # 渲染最大的
            try:
                ico = build_ico(imgs)
                tag = f"{os.path.basename(os.path.dirname(os.path.dirname(exe)))}__{label}"
                tmp = os.path.join(BASE, f"_audit_{tag}.ico")
                open(tmp, "wb").write(ico)
                im = Image.open(tmp)
                im.load()
                out = os.path.join(BASE, f"_audit_{tag}.png")
                im.convert("RGBA").resize((256, 256), Image.LANCZOS).save(out)
                print(f"    -> rendered {out} src_size={im.size}")
            except Exception as e:
                print("    render ERR", e)


if __name__ == "__main__":
    main()
