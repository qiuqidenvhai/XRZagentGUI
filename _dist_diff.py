# -*- coding: utf-8 -*-
"""dist 全树 diff：找出源里已改但 dist 没同步的文件（白名单范围）。
白名单 = build_dist.py 的 _INCLUDE_FILES + agent_core 整目录。
登录态/测试脚本不进包，跳过。"""
import hashlib, os

SRC = r"D:\软件\XianRenZhangAgent"
DIST = os.path.join(SRC, "dist", "XianRenZhangAgent")

INCLUDE_TOP = [
    "terminal.py", "terminal.pyc", "gui.html", "desktop_app.py",
    "buffer_store.py", "web_searcher.py", "subagent_main.py",
    "_files_listing_fix.py", "_sse_resilience.py", "_dom_dump_patch.py",
    "_parallel_tasks_patch.py", "_auto_update_patch.py",
    "__xianrenzhang_icon.ico", "__xianrenzhang_icon.png", "__browser_cactus_icon.ico",
]
INCLUDE_DIRS = ["agent_core"]

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def scan_dir(rel, out):
    base = os.path.join(SRC, rel)
    if not os.path.isdir(base):
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for fn in files:
            sp = os.path.join(root, fn)
            dp = os.path.join(DIST, sp[len(SRC) + 1:])
            relp = os.path.relpath(sp, SRC)
            out[relp] = (sp, dp)

todo = {}
for t in INCLUDE_TOP:
    sp = os.path.join(SRC, t)
    if os.path.exists(sp):
        dp = os.path.join(DIST, t)
        todo[t] = (sp, dp)
for d in INCLUDE_DIRS:
    scan_dir(d, todo)

diff = []
for relp, (sp, dp) in todo.items():
    if not os.path.exists(dp):
        diff.append(("DST-MISSING", relp))
    elif md5(sp) != md5(dp):
        diff.append(("DIFF", relp))

print("checked:", len(todo))
print("diff count:", len(diff))
for kind, relp in diff:
    print(" ", kind, relp)
if not diff:
    print("OK: 白名单 dist 与源 0 差异，无需同步")
