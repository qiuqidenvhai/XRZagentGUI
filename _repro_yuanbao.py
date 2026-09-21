# -*- coding: utf-8 -*-
"""复现元宝「只回一个空 done」的问题：把每一轮的事件全打出来"""
import io, os, sys, json, time, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, post_command, _open_sse, _parse, switch_platform

CMD = sys.argv[1] if len(sys.argv) > 1 else \
    "请用 file_write 工具创建文件 mp2_yuanbao.txt，内容为「yuanbao 工具测试成功」"
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 300

_h = health()
print("health:", _h)
if _h.get("platform") != "yuanbao":
    print("切换平台:", switch_platform("yuanbao"))
    time.sleep(3)
    print("health:", health())
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

print("发送:", CMD)
post_command(CMD)
types = collections.Counter()
deadline = time.time() + TIMEOUT
s.settimeout(2.0)
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
            print("   (非dict事件)", str(e)[:120])
            continue
        t = e.get("type", "")
        d = e.get("data", {}) or {}
        if not isinstance(d, dict):
            d = {"text": str(d)}
        types[t] += 1
        txt = str(d.get("text") or d.get("output") or "")
        extra = ""
        if d.get("tool"):
            extra = f" tool={d.get('tool')}"
        if t in ("ai_final_reply", "error", "agent_error"):
            print(f"[{time.strftime('%H:%M:%S')}] ★ {t}{extra}: {txt[:300]!r}")
        elif t in ("tool_start", "tool_end", "command_success", "command_detected", "correction_sent"):
            print(f"[{time.strftime('%H:%M:%S')}]   {t}{extra}: {txt[:200]!r}")
        elif t in ("thinking", "ai_thinking"):
            print(f"[{time.strftime('%H:%M:%S')}]   {t}: {txt[:160]!r}")
        if t == "ai_final_reply" and txt.strip():
            deadline = 0
            break
print("\n事件统计:", dict(types))
s.close()
