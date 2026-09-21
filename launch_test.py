#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""以 DETACHED_PROCESS 方式启动完整测试，使其独立于 WorkBuddy shell 存活（不被后续轮询命令回收）。"""
import subprocess, os

ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
LOG = os.path.join(ROOT, "full_test_run.log")

with open(LOG, "w", encoding="utf-8") as f:
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "test_full_all.py")],
        stdout=f, stderr=subprocess.STDOUT,
        creationflags=0x00000200,
    )
    f.write(f"detached pid={proc.pid}\n")
    f.flush()
print(f"launched detached pid={proc.pid} -> {LOG}")
