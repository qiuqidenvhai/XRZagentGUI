"""监控 Playwright Chromium 窗口的图标是否已被设成仙人球。

用法: python _watch_icon.py [轮数] [间隔秒]
"""
import sys
import time
import ctypes
from ctypes import wintypes

import win32gui
import win32process

BS = chr(92)
k32 = ctypes.windll.kernel32


def is_pw_chromium(pid: int) -> bool:
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        p = buf.value.lower().replace("/", BS)
        return p.endswith("chrome.exe") and "playwright_browsers" in p
    finally:
        k32.CloseHandle(h)


def snapshot():
    out = []

    def cb(hwnd, acc):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid and is_pw_chromium(pid):
                cls = win32gui.GetClassName(hwnd)
                if cls.startswith("Chrome_WidgetWin_"):
                    acc.append((
                        hwnd, pid, cls,
                        win32gui.IsWindowVisible(hwnd),
                        win32gui.GetWindowText(hwnd)[:38],
                        win32gui.SendMessage(hwnd, 0x007F, 1, 0),   # WM_GETICON ICON_BIG
                        win32gui.SendMessage(hwnd, 0x007F, 0, 0),   # ICON_SMALL
                    ))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, out)
    return out


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    for i in range(rounds):
        time.sleep(gap)
        s = snapshot()
        vis = [w for w in s if w[3]]
        done = [w for w in s if w[5] or w[6]]
        print("t=%4ds  win=%-2d 可见=%-2d 已设图标=%-2d"
              % (int((i + 1) * gap), len(s), len(vis), len(done)), flush=True)
        for w in s:
            print("        hwnd=%-9s pid=%-6s vis=%s big=%-9s small=%-9s cls=%s %r"
                  % (w[0], w[1], w[3], w[5], w[6], w[2], w[4]), flush=True)
        if s and vis and all((w[5] or w[6]) for w in vis):
            print("  ==> 所有可见窗口都已设图标", flush=True)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
