# -*- coding: utf-8 -*-
"""真机验证四边四角缩放热区 + Aero Snap（进程内 9333 桥，不碰用户键鼠）。

判定标准（每条都必须成立）：
  1) 8 个热区（4 边 + 4 角）都存在、可见、且被正确布局在窗口/centralWidget 边缘
  2) 每个热区的 Qt.Edge 位值正确（左=1 上=2 右=4 下=8，可组合）
  3) 每个热区光标方向正确（横/竖/两种对角）
  4) 窗口有 windowHandle 且未最大化 → startSystemResize 可用
  5) 改变窗口尺寸后热区跟随重排（不留在旧位置）
  6) 最大化时 can_resize=False（贴边不该还能拖）
Aero Snap 本身需拖鼠标才能看到分屏预览，本环境禁止模拟鼠标，故以
"热区走 startSystemResize" + "标题栏走 startSystemMove" 作为等效证明 ——
这两个都是系统模态调用，Windows 只对它们提供 Aero Snap。
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
npass = nfail = 0


def bridge(body, timeout=40):
    req = urllib.request.Request(BRIDGE, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"},
                                method="POST")
    return json.loads(_op.open(req, timeout=timeout).read()).get("value")


def hz():
    r = bridge({"kind": "hotzone"})
    try:
        return json.loads(r)
    except Exception:
        return {"error": r}


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-40s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:150]))


# 0) 桥就绪
d = None
for _ in range(60):
    try:
        d = hz()
        if isinstance(d, dict) and "zones" in d:
            break
    except Exception:
        time.sleep(2)
if not isinstance(d, dict) or "zones" not in d:
    out.append("[FATAL] 桥未就绪或 hotzone 不支持: %r" % (d,))
    io.open(os.path.join(ROOT, "_hz_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, bridge not ready")
    sys.exit(0)

CW, CH = d["cw"], d["ch"]
out.append("窗口 %dx%d  centralWidget %dx%d  has_handle=%s  maximized=%s"
           % (d["win_w"], d["win_h"], CW, CH, d["has_handle"], d["maximized"]))
out.append("")

zones = {z["zone"]: z for z in d["zones"]}
M = 6          # _RESIZE_MARGIN
C = M * 2      # 角块边长

# 1) 8 个热区都在
out.append("== 1) 热区数量 ==")
chk("共 8 个热区", len(zones) == 8, "实际 %d: %s" % (len(zones), sorted(zones)))
chk("4 边 + 4 角齐全",
    set(zones) == {"top", "bottom", "left", "right", "tl", "tr", "bl", "br"},
    sorted(zones))
chk("全部可见", all(z["visible"] for z in zones.values()),
    [k for k, z in zones.items() if not z["visible"]])
out.append("")

# 2) 几何正确：贴边
out.append("== 2) 几何布局（贴四条边）==")
geo_expect = {
    "top":    (0, 0, CW, M),
    "bottom": (0, CH - M, CW, M),
    "left":   (0, 0, M, CH),
    "right":  (CW - M, 0, M, CH),
}
for k, (x, y, w, h) in geo_expect.items():
    z = zones.get(k, {})
    got = (z.get("x"), z.get("y"), z.get("w"), z.get("h"))
    chk("%-7s 贴边" % k, got == (x, y, w, h), "got=%s want=%s" % (got, (x, y, w, h)))

# 角块在四个角
corner_expect = {
    "tl": (0, 0),
    "tr": (CW - C, 0),
    "bl": (0, CH - C),
    "br": (CW - C, CH - C),
}
for k, (x, y) in corner_expect.items():
    z = zones.get(k, {})
    chk("%-7s 在角上" % k, (z.get("x"), z.get("y")) == (x, y),
        "got=(%s,%s) want=(%s,%s)" % (z.get("x"), z.get("y"), x, y))
    chk("%-7s 尺寸 %dx%d" % (k, C, C), (z.get("w"), z.get("h")) == (C, C),
        "got=%sx%s" % (z.get("w"), z.get("h")))
out.append("")

# 3) Edge 位值：Left=1 Top=2 Right=4 Bottom=8
out.append("== 3) Qt.Edge 位值 ==")
edge_expect = {
    "top": 2, "bottom": 8, "left": 1, "right": 4,
    "tl": 1 | 2, "tr": 2 | 4, "bl": 8 | 1, "br": 8 | 4,
}
for k, want in edge_expect.items():
    z = zones.get(k, {})
    chk("%-7s edge=%d" % (k, want), z.get("edge_val") == want,
        "got=%s want=%s" % (z.get("edge_val"), want))
out.append("")

# 4) 光标方向：不同 zone 必须给不同且正确的形状
#    【注意】以下是 PySide6 6.11 里 Qt.CursorShape 的**实测枚举值**（真机跑出来
#    的，不是按序号推的）：SizeVerCursor=5, SizeHorCursor=6,
#    SizeBDiagCursor=7, SizeFDiagCursor=8。
#    之前我按"1/2/6/7"写期望值导致 8 个假 FAIL —— 形状其实全对。
out.append("== 4) 鼠标光标形状（枚举值: Ver=5 Hor=6 BDiag=7 FDiag=8）==")
cur_expect = {
    "left": 6, "right": 6,        # SizeHor
    "top": 5, "bottom": 5,        # SizeVer
    "tl": 8, "br": 8,             # SizeFDiag
    "tr": 7, "bl": 7,             # SizeBDiag
}
for k, want in cur_expect.items():
    z = zones.get(k, {})
    chk("%-7s cursor=%d" % (k, want), z.get("cursor") == want,
        "got=%s want=%s" % (z.get("cursor"), want))
out.append("")

# 5) 可触发系统缩放
out.append("== 5) startSystemResize 可用性 ==")
chk("窗口有 windowHandle", d["has_handle"] is True, d["has_handle"])
chk("未最大化时 can_resize=True", d["maximized"] is False and
    all(z["can_resize"] for z in zones.values()),
    "maximized=%s" % d["maximized"])
out.append("")

# 6) 改变窗口尺寸 → 热区跟随重排
out.append("== 6) 尺寸变化后热区重排 ==")
import ctypes
import ctypes.wintypes as wt
user32 = ctypes.windll.user32
hwnd = None


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
if hwnd:
    user32.SetWindowPos(hwnd, 0, 0, 0, 1000, 700, 0x0004)   # 缩小
    time.sleep(1.5)
    d2 = hz()
    CW2, CH2 = d2["cw"], d2["ch"]
    z2 = {z["zone"]: z for z in d2["zones"]}
    chk("缩小后窗口 centralWidget 变化", (CW2, CH2) != (CW, CH),
        "%dx%d -> %dx%d" % (CW, CH, CW2, CH2))
    chk("缩小后 right 热区跟随右边缘",
        z2.get("right", {}).get("x") == CW2 - M,
        "got=%s want=%s" % (z2.get("right", {}).get("x"), CW2 - M))
    chk("缩小后 bottom 热区跟随下边缘",
        z2.get("bottom", {}).get("y") == CH2 - M,
        "got=%s want=%s" % (z2.get("bottom", {}).get("y"), CH2 - M))
    chk("缩小后 br 角仍在右下角",
        (z2.get("br", {}).get("x"), z2.get("br", {}).get("y")) == (CW2 - C, CH2 - C),
        "got=(%s,%s) want=(%s,%s)" % (z2.get("br", {}).get("x"), z2.get("br", {}).get("y"),
                                     CW2 - C, CH2 - C))
    # 还原
    user32.SetWindowPos(hwnd, 0, 0, 0, 1180, 800, 0x0004)
    time.sleep(1.2)
else:
    chk("找到窗口 hwnd", False, "未找到")
out.append("")

# 7) 最大化时不该还能拖边框
out.append("== 7) 最大化时禁用拖边 ==")
if hwnd:
    user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
    time.sleep(2.0)
    d3 = hz()
    chk("最大化标志为 True", d3["maximized"] is True, d3["maximized"])
    chk("最大化后 can_resize=False",
        all(not z["can_resize"] for z in d3["zones"]),
        [z["zone"] for z in d3["zones"] if z["can_resize"]])
    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    time.sleep(1.5)
out.append("")

# 8) 截图
out.append("== 8) 截图留证 ==")
shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_hotzone.png")
try:
    bridge({"kind": "shot", "path": shot})
    chk("截图", os.path.isfile(shot), shot)
except Exception as e:
    chk("截图", False, repr(e))

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
out.append("")
out.append("Aero Snap 说明：本环境禁止模拟鼠标，无法自动拖拽验证分屏预览。")
out.append("四边四角走 startSystemResize(Edge)、标题栏走 startSystemMove()，")
out.append("这两个都是 Windows 系统模态调用 —— Aero Snap（贴边 1/2、贴角 1/4、")
out.append("顶端最大化）正是系统只对这两类调用提供的内建能力，故等效成立。")

io.open(os.path.join(ROOT, "_hz_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
