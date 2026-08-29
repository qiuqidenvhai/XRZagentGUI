"""只读诊断：枚举 chrome.exe / chrome.dll 的图标资源，确认任务栏图标来源。"""
import ctypes
from ctypes import wintypes
import os

shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

RT_ICON = 3
RT_GROUP_ICON = 14


def count_icons(path):
    """用 ExtractIconEx 统计文件内图标数量（只读，不修改）"""
    n = shell32.ExtractIconExW(str(path), -1, None, None, 0)
    return n


def enum_resources(path, rtype):
    """枚举指定类型的资源 id"""
    hmod = kernel32.LoadLibraryExW(str(path), None, 0x00000002)  # LOAD_LIBRARY_AS_DATAFILE
    if not hmod:
        return []
    found = []
    EnumResProc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.LPARAM)

    def cb(hModule, lpszType, lpszName, lParam):
        # 资源 id 小于 0x10000 时存在名字低位
        rid = ctypes.cast(lpszName, ctypes.c_void_p).value
        if rid is not None and rid < 0x10000:
            found.append(rid)
        return True

    try:
        kernel32.EnumResourceNamesW(hmod, rtype, EnumResProc(cb), 0)
    finally:
        kernel32.FreeLibrary(hmod)
    return sorted(set(found))


targets = [
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.dll",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.exe",
    r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1234\chrome-win64\chrome.dll",
]

for t in targets:
    if not os.path.exists(t):
        print(f"[缺失] {t}")
        continue
    size_mb = os.path.getsize(t) / 1024 / 1024
    n = count_icons(t)
    gi = enum_resources(t, RT_GROUP_ICON)
    icons = enum_resources(t, RT_ICON)
    print(f"{os.path.basename(t):<14} ({size_mb:7.1f} MB)  图标数={n:<4} "
          f"GROUP_ICON={gi[:8]}  RT_ICON 数量={len(icons)}")
    print(f"    路径: {t}")
