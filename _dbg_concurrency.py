#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证并发串行化：连发两条指令，确认第二条排队、两条任务轮次不交错。"""
import sys, time, json
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import post_command, _open_sse, _parse, health

A = "请用 file_write 工具创建文件 conc_a.txt，内容为「A 指令执行成功」"
B = "请用 file_write 工具创建文件 conc_b.txt，内容为「B 指令执行成功」"

print("health:", health().get("platform"), flush=True)
s, carry = _open_sse()
s.settimeout(1.0)
idle = 0; t0 = time.time()
while time.time() - t0 < 15 and idle < 3:
    try:
        ch = s.recv(65536)
        if not ch:
            break
        _, carry = _parse(ch, carry)
        idle = 0
    except Exception:
        idle += 1

print("post A:", post_command(A), flush=True)
time.sleep(2)
print("post B:", post_command(B), flush=True)

t0 = time.time()
seen = []
deadline = t0 + 420
while time.time() < deadline:
    try:
        ch = s.recv(65536)
    except Exception:
        continue
    if not ch:
        break
    evs, carry = _parse(ch, carry)
    for e in evs:
        if not isinstance(e, dict):
            continue
        t = str(e.get("type", "")); d = e.get("data", {}) or {}
        if not isinstance(d, dict):
            d = {}
        txt = str(d.get("text", ""))[:120]
        if t in ("task_started", "ai_final_reply", "tool_start", "tool_end",
                 "warning", "error", "correction_sent"):
            print(f"[{time.time()-t0:7.1f}s] {t:18s} tool={d.get('tool','')} {txt!r}", flush=True)
            seen.append(t)
    if seen.count("ai_final_reply") >= 2:
        break
print("=== events:", seen, flush=True)
