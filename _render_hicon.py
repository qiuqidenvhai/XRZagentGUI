"""把指定窗口的 WM_GETICON / GCLP_HICON 渲染成 PNG，用于肉眼/像素验收。

用法: python _render_hicon.py <hwnd> <out.png>
"""
import ctypes
import sys
import numpy as np
import win32gui
from PIL import Image, ImageDraw, ImageFont

SIZE = 200


def hicon_to_image(hicon, size=SIZE, bg=(24, 24, 24)):
    u = ctypes.windll.user32
    g = ctypes.windll.gdi32

    class BIH(ctypes.Structure):
        _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                    ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                    ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                    ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                    ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                    ('biClrImportant', ctypes.c_uint32)]

    class BI(ctypes.Structure):
        _fields_ = [('bmiHeader', BIH), ('bmiColors', ctypes.c_uint32 * 3)]

    class RECT(ctypes.Structure):
        _fields_ = [('l', ctypes.c_long), ('t', ctypes.c_long),
                    ('r', ctypes.c_long), ('b', ctypes.c_long)]

    bmi = BI()
    hh = bmi.bmiHeader
    hh.biSize = ctypes.sizeof(BIH)
    hh.biWidth = size
    hh.biHeight = -size            # 负数 = top-down
    hh.biPlanes = 1
    hh.biBitCount = 32

    bits = ctypes.c_void_p()
    g.CreateDIBSection.restype = ctypes.c_void_p
    hdc = g.CreateCompatibleDC(0)
    hbmp = g.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = g.SelectObject(hdc, ctypes.c_void_p(hbmp))

    g.CreateSolidBrush.restype = ctypes.c_void_p
    br = g.CreateSolidBrush(bg[2] << 16 | bg[1] << 8 | bg[0])
    rc = RECT(0, 0, size, size)
    u.FillRect(ctypes.c_void_p(hdc), ctypes.byref(rc), ctypes.c_void_p(br))
    g.DeleteObject(ctypes.c_void_p(br))

    u.DrawIconEx(ctypes.c_void_p(hdc), 0, 0, ctypes.c_void_p(hicon),
                 size, size, 0, None, 3)          # DI_NORMAL = 3

    buf = ctypes.string_at(bits.value, size * size * 4)
    g.SelectObject(hdc, ctypes.c_void_p(old))
    g.DeleteDC(hdc)
    g.DeleteObject(ctypes.c_void_p(hbmp))

    a = np.frombuffer(buf, dtype=np.uint8).reshape(size, size, 4)
    rgb = np.dstack([a[:, :, 2], a[:, :, 1], a[:, :, 0]]).astype(np.uint8)
    # DrawIconEx 不写 alpha；强制不透明，否则整图透明、看起来是白的
    alpha = np.full((size, size, 1), 255, np.uint8)
    return Image.fromarray(np.concatenate([rgb, alpha], axis=2), 'RGBA')


def main():
    hwnd = int(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else '_ico_scratch/render_hicon.png'

    big = win32gui.SendMessage(hwnd, 0x007F, 1, 0)
    sm = win32gui.SendMessage(hwnd, 0x007F, 0, 0)
    cbig = win32gui.GetClassLong(hwnd, -14)
    csm = win32gui.GetClassLong(hwnd, -34)
    print('hwnd=%s title=%r' % (hwnd, win32gui.GetWindowText(hwnd)[:50]))
    print('  WM_GETICON big=%s small=%s | class big=%s small=%s' % (big, sm, cbig, csm))

    items = []
    if big:
        items.append(('WM_GETICON big', hicon_to_image(big)))
    if sm:
        items.append(('WM_GETICON small', hicon_to_image(sm)))
    if cbig:
        items.append(('CLASS big', hicon_to_image(cbig)))
    target = Image.open('__browser_cactus_icon.ico').convert('RGBA')
    items.append(('目标 仙人球 ico', target))

    cell = SIZE + 26
    cv = Image.new('RGB', (cell * len(items), cell + 30), (245, 245, 248))
    d = ImageDraw.Draw(cv)
    try:
        f = ImageFont.load_default(size=14)
    except Exception:
        f = ImageFont.load_default()
    for i, (lab, im) in enumerate(items):
        x = i * cell + 14
        d.rectangle([x - 2, 6, x + SIZE + 2, 6 + SIZE + 22], fill=(255, 255, 255),
                    outline=(185, 185, 195))
        bgg = Image.new('RGBA', (SIZE, SIZE), (255, 255, 255, 255))
        bgg.alpha_composite(im.resize((SIZE, SIZE), Image.LANCZOS))
        cv.paste(bgg.convert('RGB'), (x, 28))
        d.text((x, 8), lab, fill=(20, 20, 30), font=f)
    cv.save(out)
    print('saved', out, cv.size)
    return 0


if __name__ == '__main__':
    sys.exit(main())
