"""纯 Python + ctypes 截元宝浏览器窗口（Add-Type 被沙箱拦了，改 ctypes P/Invoke）。
用法:
  python _cap_yuanbao.py list                # 列出所有可见顶层窗口
  python _cap_yuanbao.py shot <标题关键字>   # 还原+置顶该窗口，截其屏幕区域存 BMP
"""
import sys, time, struct
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

HWND = wintypes.HANDLE
BOOL = wintypes.BOOL
RECT = wintypes.RECT

EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, wintypes.HANDLE, ctypes.c_void_p)
GetTextProc = None

def enum_windows():
    res = []
    def cb(hwnd, extra):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            t = buf.value
            res.append((hwnd, t))
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return res

def find(hwnds, kw):
    return [h for h, t in hwnds if kw.lower() in t.lower()]

def capture(hwnd, out_path):
    # 置顶元宝窗口（对截图法用；对 PrintWindow 其实可省）
    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)

    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        print("窗口尺寸异常:", w, h); return False

    # 优先 PrintWindow + PW_RENDERFULLCONTENT：直接画到内存 DC，
    # 即使窗口被其它窗口遮挡也能拿到自身完整内容（对 Chromium GPU 合成画面有效）。
    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    PW_RENDERFULLCONTENT = 0x2
    ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    if not ok:
        print("PrintWindow 失败，回退到屏幕 BitBlt")
        if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, r.left, r.top, 0x00CC0020):
            print("BitBlt 也失败"); return False
    # DIB header
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    bi = BITMAPINFOHEADER()
    bi.biSize = 40; bi.biWidth = w; bi.biHeight = -h  # 负值 = top-down
    bi.biPlanes = 1; bi.biBitCount = 32; bi.biCompression = 0  # BI_RGB
    buf_size = w * h * 4
    data = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, data, ctypes.byref(bi), 0)
    # 清理
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(mem_dc); user32.ReleaseDC(0, screen_dc)
    # 写 BMP
    img = data.raw
    bw = w * 4
    line_list = [bytes(img[i*bw:(i+1)*bw]) for i in range(h)]
    line_list = list(reversed(line_list))   # BMP 要求 bottom-up
    rows = b"".join(line_list)
    pad = (-len(rows)) % 4
    out = bytearray()
    file_size = 14 + 40 + len(rows) + pad
    # BITMAPFILE header (14B): 'BM' + 文件大小 + reserved(0,0) + 数据偏移
    out += struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54 + pad)
    out += struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(rows), 2835, 2835, 0, 0)  # BITMAPINFOHEADER (40B, 11 fields)
    out += rows + b"\x00" * pad
    with open(out_path, "wb") as f:
        f.write(out)
    print(f"已截图 {out_path} ({w}x{h}, {len(out)} bytes)")
    return True

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    if mode == "list":
        print("=== 可见顶层窗口 ===")
        for h, t in enum_windows():
            if t:
                print(f"  0x{h:X}  {t}")
    elif mode == "shot":
        kw = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else r"D:\软件\XianRenZhangAgent\_yuanbao_screen.bmp"
        hwnds = enum_windows()
        hits = find(hwnds, kw)
        if not hits:
            print(f"没找到标题含「{kw}」的可见窗口；候选窗口见 list 模式")
            sys.exit(1)
        print("命中窗口:")
        for h in hits:
            print(f"  0x{h:X}")
        capture(hits[0], out)
