# -*- coding: utf-8 -*-
"""校验 desktop_app.py：语法 + 类结构完整 + 四边缩放/Aero Snap 相关符号齐全。"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(ROOT, "desktop_app.py")
out = []

# 1) 语法
try:
    tree = ast.parse(io.open(P, encoding="utf-8").read(), filename="desktop_app.py")
    out.append("[1] 语法 OK")
except SyntaxError as e:
    out.append("[1] 语法错误 L%s: %s  FAIL" % (e.lineno, e.msg))
    tree = None

# 2) 类结构完整性：类里必须有这些方法（防止缩进 0 插入把后半段吞成嵌套函数）
if tree:
    classes = {n.name: [m.name for m in n.body if isinstance(m, (ast.FunctionDef,))]
               for n in tree.body if isinstance(n, ast.ClassDef)}
    win = classes.get("XianRenZhangWindow", [])
    tb = classes.get("TitleBar", [])
    need_win = ["__init__", "nativeEvent", "resizeEvent", "eventFilter",
                "_start_backend", "apply_native_theme", "_init_test_bridge"]
    need_tb = ["__init__", "mousePressEvent", "mouseMoveEvent",
               "mouseReleaseEvent", "_on_min", "_on_max", "_on_close"]
    out.append("[2] 类 XianRenZhangWindow 方法数: %d" % len(win))
    miss = [m for m in need_win if m not in win]
    out.append("    必需方法缺失: %s  %s" % (miss or "无", "PASS" if not miss else "FAIL"))
    out.append("[2] 类 TitleBar 方法数: %d" % len(tb))
    miss2 = [m for m in need_tb if m not in tb]
    out.append("    必需方法缺失: %s  %s" % (miss2 or "无", "PASS" if not miss2 else "FAIL"))
    # 嵌套函数检查（类方法体内不应再定义同名方法）
    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, ast.FunctionDef):
                    inner = [x.name for x in ast.walk(m)
                             if isinstance(x, ast.FunctionDef) and x is not m]
                    if inner:
                        out.append("    !! %s.%s 内含嵌套函数 %s（可能被误吞）"
                                   % (n.name, m.name, inner))

# 3) 关键符号存在
src = io.open(P, encoding="utf-8").read()
checks = [
    ("WM_NCHITTEST 常量", "WM_NCHITTEST = 0x0084"),
    ("HT* 常量组", "HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT"),
    ("_hit_test 纯函数", "def _hit_test("),
    ("nativeEvent 实现", "def nativeEvent(self, eventType, message)"),
    ("最大化时不误判", "self.isMaximized() or self.isFullScreen()"),
    ("按钮区排除", "btn_zone_w"),
    ("startSystemMove", "startSystemMove()"),
    ("ctypes.wintypes 导入", "import ctypes.wintypes"),
    ("_hit_test_enabled 开关", "_hit_test_enabled = True"),
    ("_last_hit 记录", "self._last_hit = code"),
]
out.append("")
for label, needle in checks:
    ok = needle in src
    out.append("[3] %-22s %s" % (label, "PASS" if ok else "FAIL"))
    if not ok:
        pass

# 4) QSizeGrip 已移除（避免与四边判定重叠）
out.append("")
out.append("[4] QSizeGrip 实例化已移除: %s"
           % ("PASS" if "self._grip = QSizeGrip" not in src else "FAIL"))
out.append("    仍 import QSizeGrip（无害，可留）: import 处 %d"
           % src.count("QSizeGrip"))

# 5) _hit_test 单测结果
p2 = os.path.join(ROOT, "_hittest_out.txt")
if os.path.isfile(p2):
    t = io.open(p2, encoding="utf-8").read()
    out.append("")
    out.append("[5] _hit_test 单测: %s" % ("全部 PASS" if "失败 0 项" in t else "见 " + p2))

io.open(os.path.join(ROOT, "_desk_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
