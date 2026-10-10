# -*- coding: utf-8 -*-
"""用 ast 精确抽取 platform_browser.py 里的内嵌 JS 字符串常量，交给 node --check。

Python 的 py_compile 不检查内嵌 JS —— JS 写错只有真跑浏览器才会炸。
"""
import ast, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SRC = r"D:\软件\XianRenZhangAgent\agent_core\platform_browser.py"
# 【2026-10-06 修"写死本机路径"】改为动态查找 node
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _find_node import find_node
NODE = find_node()
TMP = r"D:\软件\XianRenZhangAgent\_embedded_check.js"

tree = ast.parse(open(SRC, encoding="utf-8").read())
cands = []
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        v = node.value
        if "=>" in v and ("querySelectorAll" in v or "stripTok" in v or "isEcho" in v):
            cands.append((node.lineno, v))

print(f"内嵌 JS 候选：{len(cands)} 个")
bad = 0
for ln, code in cands:
    # 这段字符串就是一个完整的箭头函数表达式，直接当作表达式校验
    open(TMP, "w", encoding="utf-8").write("const f = " + code + ";\n")
    r = subprocess.run([NODE, "--check", TMP], capture_output=True, text=True)
    if r.returncode != 0:
        # 也可能是「函数体」片段（无外层箭头）→ 再试包成函数
        open(TMP, "w", encoding="utf-8").write("function _w(){" + code + "}\n")
        r = subprocess.run([NODE, "--check", TMP], capture_output=True, text=True)
    ok = r.returncode == 0
    if not ok:
        bad += 1
    print(f"  [{'OK ' if ok else 'BAD'}] 行 {ln} 长度 {len(code)}  {r.stderr.strip()[:160]}")
print("全部通过" if bad == 0 else f"{bad} 个块语法错误")
sys.exit(0 if bad == 0 else 1)
