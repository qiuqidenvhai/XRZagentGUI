#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：原始 SSE 事件协议 + 时序（是否回放缓冲、事件是否带 id/命令关联）。"""
import socket, json, time, urllib.request

HOST, PORT = "127.0.0.1", 8888
B = "http://127.0.0.1:8888"

def open_sse():
    s = socket.create_connection((HOST, PORT), timeout=3)
    s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nAccept: text/event-stream\r\n\r\n")
    buf = b""
    while b"\r\n\r\n" not in buf:
        ch = s.recv(4096)
        if not ch: break
        buf += ch
    head, _, rest = buf.partition(b"\r\n\r\n")
    print("[headers]", head.decode("utf-8","replace").replace("\r\n"," | ")[:200], flush=True)
    return s, rest

def post(cmd):
    req = urllib.request.Request(B + "/command", data=json.dumps({"command": cmd}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try: return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    except Exception as e: return {"ERR": str(e)}

s, rest = open_sse()
print("=== 阶段1：连接后先静默 4 秒，看是否回放历史事件 ===", flush=True)
s.settimeout(1.0)
t0 = time.time(); buf = rest
while time.time() - t0 < 4:
    try:
        ch = s.recv(65536)
        if not ch: break
        buf += ch
    except socket.timeout:
        continue
for line in buf.split(b"\n"):
    line = line.strip()
    if line.startswith(b"data: "):
        try: e = json.loads(line[6:])
        except Exception: continue
        print("  [REPLAY]", e.get("type"), str(e.get("data"))[:120], flush=True)
print("=== 阶段1 结束 ===", flush=True)

print("=== 阶段2：发送命令，抓取 70 秒内所有事件 ===", flush=True)
r = post("请用一句话解释什么是 Python 装饰器（不要调用任何工具）")
print("  post resp:", r, flush=True)
t0 = time.time(); buf = b""; types = {}
while time.time() - t0 < 70:
    try:
        ch = s.recv(65536)
        if not ch: break
        buf += ch
    except socket.timeout:
        continue
    while b"\n" in buf:
        line, _, buf = buf.partition(b"\n")
        line = line.strip()
        if not line.startswith(b"data: "):
            continue
        try: e = json.loads(line[6:])
        except Exception: continue
        ty = e.get("type"); types[ty] = types.get(ty, 0) + 1
        dt = time.time() - t0
        print(f"  [+{dt:6.1f}s] {ty} :: {str(e.get('data'))[:160]}", flush=True)
        if ty == "ai_final_reply":
            print("  事件键:", list(e.keys()), "| data键:", list((e.get('data') or {}).keys()), flush=True)
print("=== 事件类型统计:", types, flush=True)
s.close()
