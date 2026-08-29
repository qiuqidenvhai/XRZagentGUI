"""枚举 chrome.exe 的图标资源 ID（只读）。"""
import os
import win32api
import win32con

LOAD_LIBRARY_AS_DATAFILE = 0x00000002

paths = [
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.exe",
]

for p in paths:
    print("=" * 70)
    print("文件:", os.path.basename(os.path.dirname(p)), "/", os.path.basename(p))
    h = win32api.LoadLibraryEx(p, 0, LOAD_LIBRARY_AS_DATAFILE)
    try:
        gi = win32api.EnumResourceNames(h, win32con.RT_GROUP_ICON)
        ri = win32api.EnumResourceNames(h, win32con.RT_ICON)
        print("  GROUP_ICON id :", sorted(gi))
        print("  RT_ICON   id  :", sorted(ri)[:16], f"  (共 {len(ri)} 个)")

        # 读取每个图标组的头部，看看各尺寸
        for gid in sorted(gi):
            data = win32api.LoadResource(h, win32con.RT_GROUP_ICON, gid)
            if not data or len(data) < 6:
                continue
            import struct
            reserved, rtype, count = struct.unpack("<HHH", data[:6])
            entries = []
            for i in range(count):
                off = 6 + i * 14
                if off + 14 > len(data):
                    break
                (w, hgt, ccount, resv, planes, bitcount,
                 bytes_in_res, img_off) = struct.unpack("<BBBBHHII", data[off:off + 16])
                entries.append((w or 256, hgt or 256, bitcount, bytes_in_res, img_off))
            print(f"    组 {gid}: {count} 个图像 -> {entries}")
    finally:
        win32api.FreeLibrary(h)
