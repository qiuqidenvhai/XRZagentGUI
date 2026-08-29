"""诊断：枚举 Playwright Chromium 窗口，看图标设置到底命中了哪些窗口。"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080


def exe_of(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(h)
    return None


def is_playwright_chrome(pid):
    p = exe_of(pid)
    if not p:
        return False, None
    low = p.lower().replace("/", "\\")
    return ((("playwright_browsers" in low) or ("ms-playwright" in low))
            and low.endswith("chrome.exe")), p


rows = []
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def cb(hwnd, lparam):
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if cls.value != "Chrome_WidgetWin_1":
        return True
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    hit, exe = is_playwright_chrome(pid.value)
    if not hit:
        return True
    visible = bool(user32.IsWindowVisible(hwnd))
    owner = user32.GetWindow(hwnd, 4)  # GW_OWNER
    parent = user32.GetParent(hwnd)
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    # 当前窗口实际用的图标句柄
    hicon_small = user32.SendMessageW(hwnd, 0x80, 0, 0)
    hicon_big = user32.SendMessageW(hwnd, 0x80, 1, 0)
    rows.append({
        "hwnd": hwnd,
        "pid": pid.value,
        "visible": visible,
        "owner": owner,
        "parent": parent,
        "appwindow": bool(exstyle & WS_EX_APPWINDOW),
        "toolwindow": bool(exstyle & WS_EX_TOOLWINDOW),
        "title": title.value[:40],
        "icon_sm": hicon_small,
        "icon_big": hicon_big,
    })
    return True


user32.EnumWindows(EnumProc(cb), 0)

print(f"Playwright Chromium 的 Chrome_WidgetWin_1 窗口数: {len(rows)}\n")
print(f"{'HWND':>10} {'PID':>7} {'可见':>4} {'owner':>8} {'parent':>8} {'APPWIN':>7} {'TOOL':>5}  图标(小/大)  标题")
print("-" * 110)
for r in rows:
    print(f"{r['hwnd']:>10} {r['pid']:>7} {str(r['visible']):>5} "
          f"{r['owner']:>8} {r['parent']:>8} {str(r['appwindow']):>7} {str(r['toolwindow']):>5}  "
          f"{r['icon_sm']:>6}/{r['icon_big']:<6}  {r['title']}")

print("\n说明：")
print("  owner/parent 为 0 = 顶层无主窗口（任务栏按钮通常由它产生）")
print("  APPWIN=True  = 带 WS_EX_APPWINDOW，会出现在任务栏")
print("  图标句柄为 0  = 该窗口从未被设置过图标（当前代码没命中它）")
