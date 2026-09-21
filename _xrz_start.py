#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以「完全脱离父进程」的方式启动仙人掌 Agent 后端（终端 8888）。

为什么需要它：用 WorkBuddy 的 run_in_background 启动时，后端挂在同一个进程树里，
一旦那个后台任务被清理（TaskStop / 任务结束），后端会被一起带走 → 测试跑到一半
「后端突然没了」。
解决：DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP，并重定向 stdout/stderr 到文件，
这样后端独立存活，不受任何后台任务生命周期影响。

用法：python _xrz_start.py        # 启动并等待就绪
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\软件\XianRenZhangAgent")
PY = r"D:\软件\Python\python.exe"
LOG = ROOT / "backend_run.log"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def health():
    try:
        return json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8888/health", timeout=3).read().decode())
    except Exception as e:
        return {"error": str(e)[:60]}


def running_pids():
    import psutil
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        if p.info["pid"] == me:
            continue
        try:
            c = p.info["cmdline"] or []
        except Exception:
            continue
        if any(str(a).endswith("terminal.py") for a in c):
            out.append(p.info["pid"])
    return out


def main():
    if health().get("status") == "ok":
        print("后端已在运行:", health())
        return
    # 清掉可能残留的僵尸后端
    import psutil
    for pid in running_pids():
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    time.sleep(2)

    env = dict(os.environ, XRZ_NO_GUI="1")
    logf = open(LOG, "ab")
    logf.write(f"\n\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 后端启动 =====\n".encode())
    logf.flush()
    p = subprocess.Popen(
        [PY, "-u", "terminal.py"],
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )
    print("已启动（脱离父进程）pid =", p.pid)

    for i in range(60):
        time.sleep(3)
        h = health()
        if h.get("status") == "ok" and h.get("agent_ready"):
            print(f"就绪（{(i + 1) * 3}s）:", h)
            return
        if i % 5 == 0:
            print(f"  等待中… {(i + 1) * 3}s  pid存活={p.poll() is None}  health={h.get('status')}/{h.get('commander')}")
    print("[失败] 后端未在 180s 内就绪，见", LOG)
    sys.exit(1)


if __name__ == "__main__":
    main()
