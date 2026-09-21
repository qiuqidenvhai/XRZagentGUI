#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""杀掉仙人掌 Agent 后端 + 所有平台 Chromium（保留 DeepSeek 浏览器可选）。

用法：python _xrz_kill.py            # 只杀后端 + 3 个网页平台浏览器
      python _xrz_kill.py all        # 连 DeepSeek 浏览器一起杀
"""
import os, sys, time, psutil

me = os.getpid()
kill_all = "all" in sys.argv
n = 0
victims = []
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    if p.info["pid"] == me:
        continue
    try:
        c = p.info["cmdline"] or []
    except Exception:
        continue
    j = " ".join(c)
    is_backend = any(a.endswith("terminal.py") for a in c)
    is_chrome = "chrome" in (p.info["name"] or "").lower() and "browser_profiles" in j
    if is_backend or is_chrome:
        if is_chrome and not kill_all and "deepseek" in j.lower():
            continue
        victims.append((p.info["pid"], (p.info["name"] or "")[:12]))

for pid, name in victims:
    try:
        psutil.Process(pid).kill()
        n += 1
        print("kill", pid, name)
    except Exception as e:
        print("  fail", pid, e)
time.sleep(3)
left = [p.info["pid"] for p in psutil.process_iter(["pid", "cmdline"])
        if any(str(a).endswith("terminal.py") for a in (p.info["cmdline"] or []))]
print("killed", n, "REMAIN_BACKEND", left)
