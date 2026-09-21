# -*- coding: utf-8 -*-
"""只杀 XRZ 的后台进程：terminal.py / test_all_platforms_full.py / _run_all.py

注意：不能用「cmdline 文本包含关键字」来匹配 —— 本脚本自己的源码里就写着
'terminal.py'，那样会把自己也杀掉（前车之鉴：整个 Bash 链被干掉、退出码 15）。
这里改成「某个 argv 元素以目标文件名结尾」，精确命中真实进程。
"""
import os
import psutil

TARGETS = ("terminal.py", "test_all_platforms_full.py", "_run_all.py")


def main():
    me = os.getpid()
    killed = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.info.get("pid") == me:
            continue
        if p.info.get("name") != "python.exe":
            continue
        cl = p.info.get("cmdline") or []
        if any(str(a).rstrip().endswith(TARGETS) for a in cl):
            try:
                p.kill()
                killed.append(p.info["pid"])
                print("killed", p.info["pid"], " ".join(cl)[:70])
            except Exception as e:
                print("kill fail", e)
    print("killed count =", len(killed))


if __name__ == "__main__":
    main()
