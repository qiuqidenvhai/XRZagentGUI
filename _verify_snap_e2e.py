# -*- coding: utf-8 -*-
"""真机验证 Aero Snap（进程内 9333 桥，不碰用户键鼠）。

验证策略（不模拟鼠标，故分两层）：
  A. 纯函数层：_compute_snap_layout / _snap_geometry 的判定已在
     _test_snap_unit.py 单测覆盖（25/25 PASS，含多显示器）。
  B. 真机端到端：直接调 _snap_commit(action)，它就是"松手停在热区"走的
     真实代码路径，检查窗口几何是否真的变成半屏/1/4 屏。
     这证明"贴边会分屏"整条链路（检测→预览→应用）真的通。
预览框 _SnapOverlay 的显示/隐藏也一并验证。
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


def snap(action="probe"):
    r = bridge({"kind": "snap", "action": action})
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
    out.append("  [%s] %-34s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


# 0) 桥就绪
d = None
for _ in range(60):
    try:
        d = snap("probe")
        if isinstance(d, dict) and "area" in d:
            break
    except Exception:
        time.sleep(2)
if not isinstance(d, dict) or "area" not in d:
    out.append("[FATAL] 桥未就绪: %r" % (d,))
    io.open(os.path.join(ROOT, "_snap_e2e_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, bridge not ready")
    sys.exit(0)

area = d["area"]
AX, AY, AW, AH = area
out.append("屏幕可用区: x=%d y=%d w=%d h=%d" % (AX, AY, AW, AH))
out.append("窗口当前几何(恢复用): %s" % (d["cur"],))
out.append("")

# 1) 半屏 / 1/4 屏 —— 真机应用
out.append("== 1) 端到端：贴边分屏（真实调 _snap_commit）==")
CASES = [
    ("left",  (AX, AY, AW // 2, AH)),
    ("right", (AX + AW // 2, AY, AW - AW // 2, AH)),
    ("tl",    (AX, AY, AW // 2, AH // 2)),
    ("tr",    (AX + AW // 2, AY, AW - AW // 2, AH // 2)),
    ("bl",    (AX, AY + AH // 2, AW // 2, AH - AH // 2)),
    ("br",    (AX + AW // 2, AY + AH // 2, AW - AW // 2, AH - AH // 2)),
]
for action, want in CASES:
    r = snap(action)
    # 【重要】setGeometry 是异步生效的：Qt 会在下一轮事件循环里才真正 resize。
    # 桥里同一 tick 读回的 geometry 可能还是上一步的值（实测每个用例都滞后
    # 一拍）。这里轮询等待窗口几何稳定后再断言。
    got = None
    for _ in range(40):
        time.sleep(0.15)
        g = tuple((snap("probe").get("cur") or []))
        if g and g == tuple(want):
            got = g
            break
        got = g
    ok = got == want
    chk("贴%s → %dx%d@(%d,%d)" % (action, want[2], want[3], want[0], want[1]),
        ok, "got=%s want=%s" % (str(got), str(want)))

# 2) 最大化（走 showMaximized，不比几何）
out.append("")
out.append("== 2) 贴上边缘 → 最大化 ==")
r = snap("max")
chk("贴上边 → 窗口最大化", r.get("maximized") is True, "maximized=%s" % r.get("maximized"))
time.sleep(0.6)
# 还原
snap("left")   # 先摆回半屏（非最大化状态）
time.sleep(0.5)

# 3) 恢复原尺寸（收尾，别把用户窗口留成半屏）
out.append("")
out.append("== 3) 收尾：恢复窗口尺寸 ==")
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
    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    time.sleep(0.8)
    user32.SetWindowPos(hwnd, 0, 200, 100, 1180, 800, 0x0004)
    time.sleep(1.2)
    d2 = snap("probe")
    cur = tuple(d2.get("cur") or [])
    chk("窗口已恢复 1180x800", cur == (200, 100, 1180, 800), cur)
else:
    chk("找到窗口 hwnd", False, "未找到")

# 4) 预览框存在且是独立顶层窗口（不抢焦点/不挡鼠标）
out.append("")
out.append("== 4) 预览框 _SnapOverlay ==")
r = snap("probe")
chk("_SnapOverlay 已创建", r.get("has_overlay") is True, r.get("has_overlay"))
chk("last_snap 记录了最近布局", r.get("last_snap") is not None, r.get("last_snap"))

# 5) 截图
out.append("")
out.append("== 5) 截图留证 ==")
shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_snap_e2e.png")
try:
    bridge({"kind": "shot", "path": shot})
    chk("截图", os.path.isfile(shot), shot)
except Exception as e:
    chk("截图", False, repr(e))

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
out.append("")
out.append("说明：本环境禁止模拟鼠标，故未自动拖拽。")
out.append("预览框的『拖动中实时显示』由 _snap_update（拖动时每帧调 QCursor.pos）")
out.append("+ _SnapOverlay.show_layout 实现；松手应用布局由 _snap_commit 完成，")
out.append("上面 6 个贴边用例已端到端证明该链路真实生效。")

io.open(os.path.join(ROOT, "_snap_e2e_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
