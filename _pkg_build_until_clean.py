# -*- coding: utf-8 -*-
"""_pkg_build_until_clean.py —— 重建分发包并逐文件校验，直到与源完全一致。

根因：本机安全软件在「大批量拷贝」时会间歇性丢弃 .py 源文件（只留 __pycache__ 的 .pyc），
导致包内 runtime 缺 urllib/xml 等标准库、后端起不来。小批量 robocopy 能正常落盘。
→ 策略：反复「整树 robocopy 重建 + 逐文件比对源」，直到零缺失（最多 N 轮）。
"""
import os
import subprocess
import sys

HERE = r"D:\软件\XianRenZhangAgent"
PKG = os.path.join(HERE, "dist", "XianRenZhangAgent")
SRC_RUNTIME = r"D:\软件\Python"
SRC_BROWSER = os.path.join(HERE, "xrz_data", "playwright_browsers")
DST_RUNTIME = os.path.join(PKG, "runtime")
DST_BROWSER = os.path.join(PKG, "xrz_data", "playwright_browsers")

# 程序文件白名单（源 = 项目根，目标 = 包根）
_INCLUDE = [
    "terminal.py", "terminal.pyc", "gui.html",
    "agent_core", "buffer_store.py", "web_searcher.py", "subagent_main.py",
    "_files_listing_fix.py", "_sse_resilience.py", "_dom_dump_patch.py",
    "_parallel_tasks_patch.py", "_auto_update_patch.py",
    "__xianrenzhang_icon.ico", "__xianrenzhang_icon.png", "__browser_cactus_icon.ico",
]


def _walk_rel(src):
    """返回源目录下所有文件的相对路径集合。"""
    out = set()
    for dp, _, fs in os.walk(src):
        for f in fs:
            out.add(os.path.relpath(os.path.join(dp, f), src))
    return out


def missing_between(src, dst):
    if not os.path.isdir(src):
        return []
    s = _walk_rel(src)
    miss = [r for r in s if not os.path.exists(os.path.join(dst, r))]
    return miss


def verify():
    m = []
    m += [("runtime", x) for x in missing_between(SRC_RUNTIME, DST_RUNTIME)]
    m += [("browser", x) for x in missing_between(SRC_BROWSER, DST_BROWSER)]
    for rel in _INCLUDE:
        s = os.path.join(HERE, rel)
        d = os.path.join(PKG, rel)
        if os.path.isdir(s):
            m += [("prog/" + rel, x) for x in missing_between(s, d)]
        elif os.path.isfile(s) and not os.path.exists(d):
            m.append(("prog", rel))
    return m


def _robo(src, dst):
    subprocess.run(
        ["robocopy", src, dst, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS"],
        capture_output=True, timeout=2400,
    )


def main():
    max_pass = 6
    for i in range(max_pass):
        print("\n===== 重建轮次 %d/%d =====" % (i + 1, max_pass), flush=True)
        # 整树重建（robocopy /E 增量补齐缺失文件）
        print("  robocopy runtime ...", flush=True); _robo(SRC_RUNTIME, DST_RUNTIME)
        print("  robocopy browsers ...", flush=True); _robo(SRC_BROWSER, DST_BROWSER)
        # 程序文件
        for rel in _INCLUDE:
            s = os.path.join(HERE, rel)
            d = os.path.join(PKG, rel)
            if os.path.isdir(s):
                _robo(s, d)
            elif os.path.isfile(s):
                os.makedirs(os.path.dirname(d) or ".", exist_ok=True)
                subprocess.run(["robocopy", os.path.dirname(s), os.path.dirname(d),
                                os.path.basename(s), "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS"],
                               capture_output=True, timeout=600)
        # 校验
        miss = verify()
        print("  缺失文件数: %d" % len(miss), flush=True)
        if miss:
            for grp, p in miss[:30]:
                print("    [%s] %s" % (grp, p), flush=True)
        else:
            print("✅ 全部文件与源一致，包完整", flush=True)
            break
    else:
        print("❌ 达到最大轮次仍有缺失，需人工介入", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
