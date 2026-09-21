# -*- coding: utf-8 -*-
"""路径修复单元验证：确认被网页渲染/弱模型插空格破坏的中文路径能自愈。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.commander import Commander

GOOD = "D:\\软件\\XianRenZhangAgent\\xrz_data\\XianRenZhang_tasks\\mp2_doubao.docx"

cases = [
    ("中文两侧插空格（实测豆包报错的那种）",
     "D:\\ 软件 \\XianRenZhangAgent\\xrz_data\\XianRenZhang_tasks\\mp2_doubao.docx"),
    ("路径被引号包住", '"' + GOOD + '"'),
    ("中间夹换行/空格", "D:\\软件\\XianRenZhangAgent\\xrz_data\\XianRenZhang_tasks\\ mp2_doubao.docx"),
    ("正斜杠混用 + 引号", '"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/mp2_doubao.docx"'),
    ("正常路径（必须原样不动）", GOOD),
]

cm = Commander.__new__(Commander)
cm._work_dir = r"D:\软件\XianRenZhangAgent"

print("=== _path_candidates ===")
for name, c in cases:
    print(f"[{name}]")
    print("  IN :", repr(c))
    for o in Commander._path_candidates(c):
        print("   ->", repr(o))
    print()

print("=== _safe_path（修复后应指向真实存在的父目录） ===")
ok = True
for name, c in cases:
    got = str(cm._safe_path(c))
    parent_ok = __import__("pathlib").Path(got).parent.exists()
    flag = "OK " if parent_ok else "BAD"
    if not parent_ok:
        ok = False
    print(f"  [{flag}] {name}\n        {got}\n        parent_exists={parent_ok}")

# 关键断言：分隔符后插入的空格必须被吃掉
_sep = str(cm._safe_path("D:\\软件\\XianRenZhangAgent\\xrz_data\\XianRenZhang_tasks\\ mp2_doubao.docx"))
if _sep.endswith("\\ mp2_doubao.docx"):
    print("  [BAD] 分隔符后的空格未被修复:", repr(_sep))
    ok = False
else:
    print("  [OK ] 分隔符后空格已修复:", repr(_sep))

# 关键断言：正常带空格的文件名不能被误改
_keep = str(cm._safe_path("D:\\软件\\XianRenZhangAgent\\mp2 report keeps space.docx"))
if "mp2 report keeps space.docx" not in _keep:
    print("  [BAD] 合法空格被误删:", repr(_keep))
    ok = False
else:
    print("  [OK ] 合法空格保留:", repr(_keep))

print()
print("全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
