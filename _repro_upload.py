# -*- coding: utf-8 -*-
"""复现 PDF 上传问答为空的问题（逐个事件打出来）"""
import io, os, sys, json, time, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, post_command, _open_sse, _parse, switch_platform

TEST_PDF = r"D:\workbuddy工作区\2026-08-11-21-39-15\test_smoke.pdf"


def upload(path):
    import urllib.request as u
    import uuid
    boundary = "----xrz" + uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="file"; filename="' \
            + os.path.basename(path).encode("utf-8") + b'"\r\n'
    body += b"Content-Type: application/pdf\r\n\r\n" + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = u.Request("http://127.0.0.1:8888/upload", data=body, method="POST",
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(u.urlopen(req, timeout=60).read().decode())


_h = health()
print("health:", _h)
if _h.get("platform") != "deepseek":
    print("切换:", switch_platform("deepseek"))
    time.sleep(3)
print("upload:", upload(TEST_PDF))

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

CMD = "请阅读刚才上传的 PDF，用一句话概括它的主要内容"
print("发送:", CMD)
post_command(CMD)
types = collections.Counter()
deadline = time.time() + 240
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
            continue
        t = e.get("type", "")
        d = e.get("data", {}) or {}
        if not isinstance(d, dict):
            d = {"text": str(d)}
        types[t] += 1
        txt = str(d.get("text") or d.get("output") or "")
        extra = f" tool={d.get('tool')}" if d.get("tool") else ""
        if t in ("ai_final_reply", "error", "agent_error"):
            print(f"★ {t}: {txt[:400]!r}")
        elif t in ("tool_start", "tool_end", "command_success", "command_detected", "correction_sent"):
            print(f"  {t}{extra}: {txt[:250]!r}")
        elif t in ("thinking", "ai_thinking"):
            print(f"  {t}: {txt[:120]!r}")
        if t == "ai_final_reply" and txt.strip():
            deadline = 0
            break
print("\n事件统计:", dict(types))
s.close()
