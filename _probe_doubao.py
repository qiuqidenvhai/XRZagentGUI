#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：切到豆包发一条消息，触发发送诊断（约 5s 后写日志），随后读日志分析。"""
import sys, time
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, switch_platform, post_command, deepseek_count

for _ in range(40):
    h = health()
    if h.get("agent_ready") and h.get("browser") == "connected":
        print("backend ready:", h, flush=True); break
    time.sleep(5)
else:
    print("backend not ready", h, flush=True); raise SystemExit(1)

print("deepseek 实例数:", deepseek_count(), flush=True)
print("switch doubao:", switch_platform("doubao"), flush=True)
time.sleep(4)
print("health.platform =", health().get("platform"), flush=True)
print("post:", post_command("你好，请用一句话回复"), flush=True)
time.sleep(50)
print("等待完成，开始读日志", flush=True)
