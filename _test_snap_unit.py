# -*- coding: utf-8 -*-
"""单测 Aero Snap 纯函数：_compute_snap_layout + _snap_geometry。

用 1920x1040（1080 屏减去 40px 任务栏）作为屏幕可用区，覆盖 9 种判定：
  6 个分屏热区（left/right/max/tl/tr/bl/br）+ 3 个非热区（应返回 None）
并校验每种布局算出的几何是否精确对齐半屏/1/4 屏。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

src = io.open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read()
m = re.search(r"def _compute_snap_layout[\s\S]*?\n    return None\n", src)
m2 = re.search(r"def _snap_geometry[\s\S]*?\n    return None\n", src)
if not (m and m2):
    raise SystemExit("未能从 desktop_app.py 提取 snap 纯函数")
code = "_SNAP_EDGE = 12\n" + m.group(0) + "\n" + m2.group(0)
ns = {}
exec(compile(code, "<snap>", "exec"), ns)
_csl = ns["_compute_snap_layout"]
_sg = ns["_snap_geometry"]

W, H = 1920, 1040
AREA = (0, 0, W, H)
E = ns["_SNAP_EDGE"]

out = []
fails = []


def chk(label, got, want):
    ok = got == want
    if not ok:
        fails.append(label)
    out.append("  %-30s got=%-22s want=%-22s %s"
               % (label, str(got), str(want), "PASS" if ok else "FAIL"))


out.append("屏幕可用区: %dx%d  边缘阈值=%d" % (W, H, E))
out.append("")
out.append("== 1) 热区判定（鼠标停在各边缘）==")
chk("贴左边缘", _csl(3, 500, AREA), "left")
chk("贴右边缘", _csl(W - 3, 500, AREA), "right")
chk("贴上边缘", _csl(900, 2, AREA), "max")
chk("左上角", _csl(2, 2, AREA), "tl")
chk("右上角", _csl(W - 2, 2, AREA), "tr")
chk("左下角", _csl(2, H - 2, AREA), "bl")
chk("右下角", _csl(W - 2, H - 2, AREA), "br")
out.append("")
out.append("== 2) 非热区应返回 None（不能乱吸附）==")
chk("正中", _csl(960, 520, AREA), None)
chk("靠下但不贴边", _csl(960, H - 40, AREA), None)
chk("靠上但不贴边", _csl(960, 40, AREA), None)
chk("靠左但不贴边", _csl(40, 520, AREA), None)
chk("靠右但不贴边", _csl(W - 40, 520, AREA), None)
out.append("")
out.append("== 3) 布局几何（是否精确半屏/1/4）==")
chk("left  左半屏", _sg("left", AREA), (0, 0, 960, 1040))
chk("right 右半屏", _sg("right", AREA), (960, 0, 960, 1040))
chk("max   最大化", _sg("max", AREA), (0, 0, 1920, 1040))
chk("tl    左上1/4", _sg("tl", AREA), (0, 0, 960, 520))
chk("tr    右上1/4", _sg("tr", AREA), (960, 0, 960, 520))
chk("bl    左下1/4", _sg("bl", AREA), (0, 520, 960, 520))
chk("br    右下1/4", _sg("br", AREA), (960, 520, 960, 520))
out.append("")
out.append("== 4) 多显示器/副屏偏移（几何应随 area 平移）==")
AREA2 = (1920, 0, 1280, 960)
chk("副屏贴左→左半屏", _sg("left", AREA2), (1920, 0, 640, 960))
chk("副屏左上角", _sg("tl", AREA2), (1920, 0, 640, 480))
chk("副屏贴左判定", _csl(1922, 500, AREA2), "left")
chk("副屏正中不吸附", _csl(2560, 480, AREA2), None)
out.append("")
out.append("== 5) 非法输入 ==")
chk("area=None 不崩", _csl(10, 10, None), None)
chk("layout=None 几何", _sg(None, AREA), None)
chk("未知 layout", _sg("nope", AREA), None)

out.append("")
out.append("失败 %d 项" % len(fails))
out.append("结论: %s" % ("全部 PASS" if not fails else "FAIL -> " + ", ".join(fails)))

io.open(os.path.join(ROOT, "_snap_unit_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, fails=%d" % len(fails))
