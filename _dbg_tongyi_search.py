#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现 tongyi browser_search 空最终回复：打印全部 SSE 事件（带时间戳）"""
import sys, time, json, collections
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import switch_platform, post_command, _open_sse, _parse, health

CMD = "请用 browser_search 工具搜索「Python asyncio 官方文档」，返回最相关的一个链接标题"
TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 300

print("health:", health(), flush=True)
print("switch:", switch_platform("tongyi"), flush=True)
time.sleep(3)
print("health:", health().get("platform"), flush=True)

s, carry = _open_sse()
s.settimeout(1.0)
idle = 0; t0 = time.time()
while time.time() - t0 < 20 and idle < 3:
    try:
        ch = s.recv(65536)
        if not ch:
            break
        _, carry = _parse(ch, carry)
        idle = 0
    except Exception:
        idle += 1

print("post:", post_command(CMD), flush=True)
t0 = time.time()
s.settimeout(2.0)
tools = []
deadline = t0 + TIMEOUT
while time.time() < deadline:
    try:
        ch = s.recv(65536)
    except Exception:
        continue
    if not ch:
        print("!! SSE closed", flush=True)
        break
    evs, carry = _parse(ch, carry)
    for e in evs:
        t = e.get("type", ""); d = e.get("data", {}) or {}
        if not isinstance(d, dict):
            print(f"[{time.time()-t0:7.1f}s] {t:24s} <raw> {str(d)[:200]!r}", flush=True)
            continue
        dt = time.time() - t0
        txt = str(d.get("text", d.get("output", d.get("message", ""))))[:200]
        extra = ""
        if t in ("tool_start", "tool_end"):
            extra = f"tool={d.get('tool')} status={d.get('status')}"
        print(f"[{dt:7.1f}s] {t:24s} {extra} {txt!r}", flush=True)
        if t in ("tool_start",):
            tools.append(d.get("tool"))
print(f"=== done. tools={tools} elapsed={time.time()-t0:.1f}s", flush=True)
