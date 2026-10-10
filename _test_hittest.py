# -*- coding: utf-8 -*-
"""单测 _hit_test：四边四角命中 + 标题栏 + 按钮区排除。

不依赖 Qt，纯函数验证。构造一个 1180x800 的窗口。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 只导入 _hit_test 与常量，避免拉起整个 Qt 应用
import importlib.util
spec = importlib.util.spec_from_file_location("_dt_head", os.path.join(ROOT, "desktop_app.py"))

# 直接从源码里抠出常量段与 _hit_test 函数执行（避免 import PySide6/QWebEngine）
import re
src = io.open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read()
# 只取 WM_NCHITTEST 定义开始，到 _hit_test 函数结束（非贪婪 + 明确终点）
m = re.search(r"WM_NCHITTEST = 0x0084[\s\S]*?\n    return HTCLIENT\n", src)
if not m:
    raise SystemExit("未能从 desktop_app.py 提取命中测试代码段")
code = m.group(0)
ns = {}
exec(compile(code, "<hit_test>", "exec"), ns)
_hit_test = ns["_hit_test"]
C = {k: v for k, v in ns.items() if k.startswith(("HT", "WM_"))}

W, H = 1180, 800
M = ns["_RESIZE_MARGIN"]
CH = ns["_CAPTION_HEIGHT"]
BTN = 3 * 34 + 4 * 6 + 8

out = []
fails = []


def chk(label, got, want):
    ok = got == want
    if not ok:
        fails.append(label)
    out.append("  %-34s got=%-4s want=%-4s %s"
               % (label, C and got or got, C and want or want,
                  "PASS" if ok else "FAIL"))


out.append("窗口 %dx%d  margin=%d  caption_h=%d  btn_zone=%d" % (W, H, M, CH, BTN))
out.append("")
out.append("-- 四边 --")
chk("左边缘中点", _hit_test(2, H // 2, W, H), C["HTLEFT"])
chk("右边缘中点", _hit_test(W - 2, H // 2, W, H), C["HTRIGHT"])
chk("上边缘中点", _hit_test(W // 2, 2, W, H), C["HTTOP"])
chk("下边缘中点", _hit_test(W // 2, H - 2, W, H), C["HTBOTTOM"])
out.append("-- 四角 --")
chk("左上", _hit_test(1, 1, W, H), C["HTTOPLEFT"])
chk("右上", _hit_test(W - 1, 1, W, H), C["HTTOPRIGHT"])
chk("左下", _hit_test(1, H - 1, W, H), C["HTBOTTOMLEFT"])
chk("右下", _hit_test(W - 1, H - 1, W, H), C["HTBOTTOMRIGHT"])
out.append("-- 标题栏 --")
chk("标题栏左区(拖动)", _hit_test(200, 15, W, H), C["HTCAPTION"])
chk("标题栏中区(拖动)", _hit_test(W // 2, 20, W, H), C["HTCAPTION"])
chk("标题栏图标处", _hit_test(20, 10, W, H), C["HTCAPTION"])
out.append("-- 按钮区必须让给 Qt --")
chk("最右(关闭按钮)", _hit_test(W - 15, 15, W, H), C["HTCLIENT"])
chk("最大化按钮", _hit_test(W - 45, 15, W, H), C["HTCLIENT"])
chk("最小化按钮", _hit_test(W - 80, 15, W, H), C["HTCLIENT"])
chk("按钮区左界外(应可拖)", _hit_test(W - BTN - 8, 15, W, H), C["HTCAPTION"])
out.append("-- 内容区 --")
chk("正中", _hit_test(W // 2, H // 2, W, H), C["HTCLIENT"])
chk("内容区靠下", _hit_test(W // 2, H - 100, W, H), C["HTCLIENT"])
chk("标题栏下方1px", _hit_test(200, CH + 1, W, H), C["HTCLIENT"])

out.append("")
out.append("失败 %d 项" % len(fails))
out.append("结论: %s" % ("全部 PASS" if not fails else "FAIL -> " + ", ".join(fails)))

io.open(os.path.join(ROOT, "_hittest_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, fails=%d" % len(fails))
