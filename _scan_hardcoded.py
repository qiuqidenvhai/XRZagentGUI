# -*- coding: utf-8 -*-
"""全项目复查：还有没有写死的开发者用户名 / 固定用户桌面路径。

排除：build_tools/（第三方 PyInstaller）、.workbuddy/、dist/、__pycache__、
xrz_data/、build/，以及注释/docstring（注释里作为"反面案例"说明可保留）。
"""
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {
    "build_tools", "dist", "__pycache__", ".workbuddy", "xrz_data",
    "build", ".git", "node_modules", "_github_icons", "test_gui_profile",
}
# 这些是开发期工具（不随包分发），但仍应尽量无写死路径；单独归类
DEVTOOLS = {"_check_gui_js.py", "_lint_gui_js.py", "_check_embedded_js.py",
            "_verify_gui_fixes.py"}

out = []
prod_hits = []
dev_hits = []
checked = 0

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if not fn.endswith((".py", ".html", ".bat", ".json", ".js")):
            continue
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, ROOT)
        try:
            s = io.open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        checked += 1
        for i, line in enumerate(s.split("\n"), 1):
            if "X.LAPTOP-CA1GJQE3" not in line:
                continue
            st = line.strip()
            # 注释 / docstring 里的反面案例说明：不算问题
            if st.startswith("#") or st.startswith('"') or st.startswith("*") \
               or st.startswith("'''") or "【" in st or "旧代码" in st \
               or "写死" in st or "修\"" in st:
                continue
            rec = "%s:%d  %s" % (rel, i, st[:110])
            (dev_hits if fn in DEVTOOLS else prod_hits).append(rec)

out.append("扫描文件数: %d" % checked)
out.append("")
out.append("=== 产品代码(会发给用户/随包分发) 硬编码开发者用户名: %d 处 ===" % len(prod_hits))
out.extend("  " + h for h in prod_hits)
out.append("")
out.append("=== 开发期自测脚本 硬编码: %d 处 ===" % len(dev_hits))
out.extend("  " + h for h in dev_hits)

# 额外：扫固定 C:\Users\ 但不是本机用户名的（那种才是真正的"写死别人电脑"）
out.append("")
out.append("=== 其它固定 C:\\Users\\ 引用（非本机用户名，疑似写死） ===")
other = 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in filenames:
        if not fn.endswith((".py", ".html", ".bat")):
            continue
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, ROOT)
        try:
            s = io.open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(s.split("\n"), 1):
            if "C:\\Users\\" in line or "C:/Users/" in line:
                st = line.strip()
                if "X.LAPTOP-CA1GJQE3" in st:
                    continue          # 本机用户名，已在上面统计
                if "<用户名>" in st or "<user>" in st.lower():
                    continue          # 占位符，OK
                other += 1
                out.append("  %s:%d  %s" % (rel, i, st[:110]))
out.append("  合计: %d" % other)

io.open(os.path.join(ROOT, "_scan_paths_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
