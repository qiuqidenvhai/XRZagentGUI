"""
把仙人掌图标写入 Playwright Chromium 的 chrome.exe 资源（替换 IDR_MAINFRAME 主图标）。

背景：Chromium 标题栏是自绘的，且任务栏图标取自「启动时 exe 的资源图标」，
因此只发 WM_SETICON 改不掉任务栏/标题栏图标，必须改 exe 的图标资源。

用法：
    python patch_chrome_icon.py            # dry-run，只解析不写入
    python patch_chrome_icon.py --apply    # 实际写入（会先自动备份）
"""
import os
import struct
import shutil
import sys

import win32api
import win32con

ICON = r"D:\软件\XianRenZhangAgent\__browser_cactus_icon.ico"

CHROMES = [
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.exe",
]

GROUP_NAME = "IDR_MAINFRAME"
NEW_ICON_BASE = 100          # 新 RT_ICON 起始 id（避开原有 1~33）
LOAD_LIBRARY_AS_DATAFILE = 0x00000002


def parse_ico(path):
    """解析 .ico，返回 [(w, h, bitcount, data_bytes), ...]"""
    raw = open(path, "rb").read()
    reserved, rtype, count = struct.unpack("<HHH", raw[:6])
    assert rtype == 1, f"不是图标文件: type={rtype}"
    out = []
    for i in range(count):
        off = 6 + i * 16
        (bw, bh, ccount, _resv, planes, bitcount,
         bytes_in_res, img_off) = struct.unpack("<BBBBHHII", raw[off:off + 16])
        w = bw or 256
        h = bh or 256
        data = raw[img_off:img_off + bytes_in_res]
        assert len(data) == bytes_in_res, f"图像 {i} 数据截断"
        out.append((w, h, bitcount, data))
    return out


def build_group(images, base_id):
    """构造 RT_GROUP_ICON 资源数据（GRPICONDIR + GRPICONDIRENTRY 每条 14 字节）"""
    buf = struct.pack("<HHH", 0, 1, len(images))
    for i, (w, h, bitcount, data) in enumerate(images):
        # bWidth/bHeight 为 0 表示 256
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        buf += struct.pack("<BBBBHHIH",
                           bw, bh, 0, 0,
                           1, bitcount,
                           len(data),
                           base_id + i)      # 注意: WORD nId（2字节），不是 4 字节偏移
    return buf


def main(apply=False):
    images = parse_ico(ICON)
    print(f"仙人掌图标: {len(images)} 个尺寸 -> "
          f"{[(w, h) for w, h, _, _ in images]}")
    group_data = build_group(images, NEW_ICON_BASE)
    print(f"GRPICONDIR 长度: {len(group_data)} 字节 "
          f"(应为 6 + {len(images)}*14 = {6 + len(images) * 14})")

    for chrome in CHROMES:
        print("\n" + "=" * 66)
        print("目标:", chrome)
        if not os.path.exists(chrome):
            print("  [跳过] 文件不存在")
            continue

        # 查询原资源语言，确保是「替换」而不是「新增」
        h = win32api.LoadLibraryEx(chrome, 0, LOAD_LIBRARY_AS_DATAFILE)
        try:
            langs = win32api.EnumResourceLanguages(h, win32con.RT_GROUP_ICON, GROUP_NAME)
            have_icons = win32api.EnumResourceNames(h, win32con.RT_ICON)
        finally:
            win32api.FreeLibrary(h)
        lang = langs[0] if langs else 0
        print(f"  原 {GROUP_NAME} 语言={lang}  现有 RT_ICON 数={len(have_icons)}")

        if not apply:
            print("  [dry-run] 未写入。加 --apply 实际执行。")
            continue

        # 先备份（若备份不存在）
        bak = chrome + ".cactus_backup"
        if not os.path.exists(bak):
            shutil.copy2(chrome, bak)
            print(f"  已备份 -> {os.path.basename(bak)}")

        hu = win32api.BeginUpdateResource(chrome, 0)
        try:
            for i, (_w, _h, _b, data) in enumerate(images):
                win32api.UpdateResource(hu, win32con.RT_ICON,
                                        NEW_ICON_BASE + i, data, lang)
            win32api.UpdateResource(hu, win32con.RT_GROUP_ICON,
                                    GROUP_NAME, group_data, lang)
            win32api.EndUpdateResource(hu, 0)
        except Exception as e:
            win32api.EndUpdateResource(hu, True)   # 放弃写入
            print(f"  [失败] {e}")
            continue
        print("  已写入仙人掌图标 ✔")


if __name__ == "__main__":
    main(apply=("--apply" in sys.argv))
