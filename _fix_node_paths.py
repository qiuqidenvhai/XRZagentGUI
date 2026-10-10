# -*- coding: utf-8 -*-
"""把 4 个自测脚本里写死的 node.exe 绝对路径换成 _find_node.find_node()。"""
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

JOBS = {
    "_check_gui_js.py": (
        'NODE = r"C:\\Users\\X.LAPTOP-CA1GJQE3\\.workbuddy\\binaries\\node\\versions\\22.22.2-3\\node.exe"',
        'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
        '# 【2026-10-06 修"写死本机路径"】原来写死某个 node 版本/用户目录的绝对路径，\n'
        '# 换机器或 node 升级就失效。改为动态查找（NODE 环境变量 -> PATH -> 常见位置）。\n'
        'sys.path.insert(0, ROOT)\n'
        'from _find_node import find_node\n'
        'NODE = find_node()',
    ),
    "_lint_gui_js.py": (
        'NODE = "C:/Users/X.LAPTOP-CA1GJQE3/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"',
        '# 【2026-10-06 修"写死本机路径"】改为动态查找 node\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from _find_node import find_node\n'
        'NODE = find_node()',
    ),
    "_check_embedded_js.py": (
        'NODE = r"C:\\Users\\X.LAPTOP-CA1GJQE3\\.workbuddy\\binaries\\node\\versions\\22.22.2-3\\node.exe"',
        '# 【2026-10-06 修"写死本机路径"】改为动态查找 node\n'
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        'from _find_node import find_node\n'
        'NODE = find_node()',
    ),
    "_verify_gui_fixes.py": (
        'node = r"C:\\Users\\X.LAPTOP-CA1GJQE3\\.workbuddy\\binaries\\node\\versions\\22.22.2-3\\node.exe"',
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
        '    from _find_node import find_node\n'
        '    node = find_node()',
    ),
}

log = []
for fn, (old, new) in JOBS.items():
    p = os.path.join(ROOT, fn)
    if not os.path.isfile(p):
        log.append("%-24s SKIP(不存在)" % fn)
        continue
    s = io.open(p, encoding="utf-8").read()
    if old not in s:
        log.append("%-24s !! 未找到待替换片段" % fn)
        continue
    s = s.replace(old, new)
    io.open(p, "w", encoding="utf-8").write(s)
    left = s.count("X.LAPTOP-CA1GJQE3")
    log.append("%-24s 已替换 | 剩余硬编码 %d" % (fn, left))

io.open(os.path.join(ROOT, "_fix_node_out.txt"), "w", encoding="utf-8").write("\n".join(log))
print("done")
