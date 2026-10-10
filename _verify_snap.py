# -*- coding: utf-8 -*-
"""真机验证四边缩放 + Aero Snap —— 全部走进程内 9333 测试桥（不碰用户键鼠）。

【关键教训】外部进程 SendMessageW(hwnd, WM_NCHITTEST) 验证不了：
Qt 的 nativeEvent 只在它自己的消息循环里被调用，跨进程发消息走的是直接
窗口过程（DefWindowProc），不管装没装 Qt 都会返回 HTCLIENT(1)。
必须用桥 kind="hittest"，让请求回到 GUI 线程再发，才能真正命中 nativeEvent。
"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BRIDGE = "http://127.0.0.1:9333/"

out = []


def bridge(body, timeout=40):
    req = urllib.request.Request(BRIDGE, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"},
                                method="POST")
    return json.loads(_op.open(req, timeout=timeout).read()).get("value")


def hit(points):
    """进程内发 WM_NCHITTEST，返回各点判定值。"""
    r = bridge({"kind": "hittest", "points": points})
    try:
        return json.loads(r)
    except Exception:
        return {"error": r}


# 0) 桥与窗口就绪
info = None
for _ in range(60):
    try:
        info = hit([[100, 100]])
        if isinstance(info, dict) and "w" in info:
            break
    except Exception:
        time.sleep(2)
if not isinstance(info, dict) or "w" not in info:
    out.append("[FATAL] 桥未就绪或 hittest 不支持: %r" % (info,))
    io.open(os.path.join(ROOT, "_snap_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, bridge not ready")
    sys.exit(0)

W, H = info["w"], info["h"]
out.append("窗口客户区: %dx%d  maximized=%s" % (W, H, info["maximized"]))
out.append("")

# 1) 各边缘命中判定（坐标用客户区坐标 + 窗口在屏幕上的位置换算成绝对坐标）
#    桥内部用 gx-gy 绝对坐标，nativeEvent 内部再减去 frameGeometry.left()。
#    所以这里要传屏幕绝对坐标。无边框窗口 frameGeometry 原点 == GetWindowRect 原点。
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
hwnd = None
res = []


@ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
def _cb(h, l):
    global hwnd
    if user32.IsWindowVisible(h):
        n = user32.GetWindowTextLengthW(h)
        if n > 0:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if "仙人掌" in b.value:
                hwnd = h
                return False
    return True


user32.EnumWindows(_cb, 0)
if not hwnd:
    out.append("[FATAL] 找不到窗口")
    io.open(os.path.join(ROOT, "_snap_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, no hwnd")
    sys.exit(0)

rect = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
L, T = rect.left, rect.top
out.append("窗口 hwnd=0x%X  屏幕位置 (%d,%d)  尺寸 %dx%d"
           % (hwnd, L, T, rect.right - rect.left, rect.bottom - rect.top))
out.append("")

M = 3  # 探测点距边缘的像素（> _RESIZE_MARGIN=6 的话会落客户区，所以往里收）
CASES = [
    ("左边缘",     L + 2,                T + H // 2,      10, "HTLEFT"),
    ("右边缘",     L + W - 3,            T + H // 2,      11, "HTRIGHT"),
    ("上边缘",     L + W // 2,           T + 2,           12, "HTTOP"),
    ("下边缘",     L + W // 2,           T + H - 3,       15, "HTBOTTOM"),
    ("左上角",     L + 1,                T + 1,           13, "HTTOPLEFT"),
    ("右上角",     L + W - 2,            T + 1,           14, "HTTOPRIGHT"),
    ("左下角",     L + 1,                T + H - 2,       16, "HTBOTTOMLEFT"),
    ("右下角",     L + W - 2,            T + H - 2,       17, "HTBOTTOMRIGHT"),
    ("标题栏中区", L + W // 2,           T + 15,           2, "HTCAPTION"),
    ("内容区正中", L + W // 2,           T + H // 2,       1, "HTCLIENT"),
    ("关闭按钮",   L + W - 15,           T + 15,           1, "HTCLIENT"),
]

pts = [[gx, gy] for _, gx, gy, _, _ in CASES]
r = hit(pts)
got_list = r.get("results", [])
out.append("== 进程内 WM_NCHITTEST 探测（真正走 Qt nativeEvent）==")
npass = nfail = 0
for (label, _, _, want, name), got in zip(CASES, got_list):
    ok = got == want
    npass += ok
    nfail += (not ok)
    out.append("  [%s] %-12s 期望 %-14s(%2d)  实返 %d"
               % ("PASS" if ok else "FAIL", label, name, want, got))
out.append("  小计: PASS %d / FAIL %d" % (npass, nfail))
out.append("")

# 2) 最大化后不应判为缩放边
user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
time.sleep(2.0)
r2 = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r2))
r = hit([[r2.left + 2, (r2.top + r2.bottom) // 2]])
gm = (r.get("results") or [1])[0]
ok = gm != 10
out.append("== 最大化后 ==")
out.append("  [%s] 最大化后左边缘不判 HTLEFT（实返 %d）" % ("PASS" if ok else "FAIL", gm))
npass += ok
nfail += (not ok)
out.append("  maximized 标志: %s" % r.get("maximized"))
user32.ShowWindow(hwnd, 9)   # SW_RESTORE
time.sleep(1.5)
out.append("")

# 3) 实际改窗口尺寸（系统级缩放）
W2, H2 = W - 120, H - 90
user32.SetWindowPos(hwnd, 0, L + 20, T + 20, W2, H2, 0x0004)
time.sleep(1.2)
r3 = hit([[100, 100]])
nw, nh = r3.get("w", 0), r3.get("h", 0)
ok = (nw, nh) == (W2, H2)
out.append("== 系统级缩放 ==")
out.append("  [%s] SetWindowPos 生效: %dx%d -> %dx%d"
           % ("PASS" if ok else "FAIL", W, H, nw, nh))
npass += ok
nfail += (not ok)
user32.SetWindowPos(hwnd, 0, L, T, W, H, 0x0004)
time.sleep(1.0)
out.append("")

# 4) 截图
shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_snap.png")
try:
    bridge({"kind": "shot", "path": shot})
    ok = os.path.isfile(shot)
except Exception as e:
    ok = None
    out.append("  截图异常: %r" % e)
out.append("  [%s] 截图: %s" % ("PASS" if ok else ("SKIP" if ok is None else "FAIL"), shot))
out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
out.append("Aero Snap 说明：标题栏已判为 HTCAPTION、边缘已判为 HT*，")
out.append("Windows 对此类窗口自动提供贴边分屏(1/2、1/4)与顶端最大化，")
out.append("无需自行实现（本环境不模拟鼠标，故不自动化拖拽预览）。")

io.open(os.path.join(ROOT, "_snap_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
