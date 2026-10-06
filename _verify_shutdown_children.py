# -*- coding: utf-8 -*-
"""真机验证「关 GUI 连带清理」逻辑：调用 desktop_app 的
shutdown_backend_and_children()（dry_run=False）真执行一次，
记录清理前后 8888 端口监听 PID + 残留 chrome/node 进程数。
（当前 7296 是活跃后端，执行后需再重启后端恢复服务。）"""
import os, sys, logging, time
logging.basicConfig(level=logging.INFO, format="%(message)s")
import importlib.util
spec = importlib.util.spec_from_file_location("da", os.path.abspath("desktop_app.py"))
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)   # 触发 import（PySide + agent_core），不跑 main

import psutil

def snapshot(label):
    cons = [c for c in psutil.net_connections(kind="tcp")
            if c.laddr and c.laddr[1] == 8888 and c.status == "LISTEN"]
    pids = sorted({c.pid for c in cons})
    # 数残留 chrome/node（按 cmdline 特征）
    chrome = node = 0
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            cl = ""
        if "chromium-" in cl or (p.info.get("name") == "chrome.exe" and "playwright_browsers" in cl):
            chrome += 1
        if "playwright\\driver\\node.exe" in cl or "playwright/driver/node.exe" in cl:
            node += 1
    print(f"[{label}] 8888 LISTEN pids={pids} | chrome={chrome} node={node}", flush=True)

print("=" * 60)
snapshot("BEFORE")
da.shutdown_backend_and_children(dry_run=False)
time.sleep(3.0)
snapshot("AFTER")
print("done")
