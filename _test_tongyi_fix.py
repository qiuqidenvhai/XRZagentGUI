#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「疑似未完整」修复后，通义(千问)对话能否正常返回。"""
import sys, time
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, run, switch_platform, deepseek_count

# 等待后端就绪
for _ in range(30):
    h = health()
    if h.get("agent_ready") and h.get("browser") == "connected":
        print("backend ready:", h, flush=True); break
    time.sleep(5)
else:
    print("backend not ready:", h, flush=True); raise SystemExit(1)

print("deepseek 实例数(基线):", deepseek_count(), flush=True)
print("切换:", switch_platform("tongyi"), flush=True)
time.sleep(3)
print("health.platform =", health().get("platform"), "| deepseek实例数 =", deepseek_count(), flush=True)

t0 = time.time()
r = run("请用一句话说明你是什么模型（不要调用任何工具）", 240)
print(f"\n== tongyi chat 耗时{time.time()-t0:.0f}s ==", flush=True)
print(" ok=", r["ok"], flush=True)
print(" task_id=", r["task_id"], flush=True)
print(" tools=", sorted(r["tools"]), flush=True)
print(" types=", dict(r["types"]), flush=True)
print(" final=", r["final"][:300], flush=True)
print(" errs=", r["errs"][:2], flush=True)
print("\ndeepseek 实例数(终):", deepseek_count(), flush=True)

print("切换回:", switch_platform("deepseek"), flush=True)
time.sleep(2)
print("health.platform =", health().get("platform"), "| deepseek实例数 =", deepseek_count(), flush=True)
