# -*- coding: utf-8 -*-
"""
针对性复验三项：
  1) PDF 上传问答：像 GUI 一样把附件路径一起发过去，模型应该能读到并概括
  2) 子代理：派发后应能看到工具事件 + 最终回复
  3) 元宝未登录：应快速返回干净的「[需要登录]」，而不是 15s/210s 超时 + Playwright Call log
"""
import json, os, sys, time, collections, socket

sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import (health, run, switch_platform, post_command,
                          deepseek_count, _open_sse, _parse)

BASE_DS = deepseek_count()
print(f"基线 DeepSeek 实例数 = {BASE_DS}", flush=True)
print("health:", health(), flush=True)

# ───────── 1) PDF 上传问答 ─────────
print("\n===== 1) PDF 上传问答（带 attachments，与 GUI 一致）=====", flush=True)
switch_platform("deepseek"); time.sleep(2)
TEST_PDF = r"D:\workbuddy工作区\2026-08-11-21-39-15\test_smoke.pdf"


def upload_pdf(path, timeout=120):
    import urllib.request as ur
    boundary = "----xrzboundary"
    with open(path, "rb") as f:
        data = f.read()
    body = (("--" + boundary + "\r\n").encode()
            + b'Content-Disposition: form-data; name="file"; filename="' + os.path.basename(path).encode() + b'"\r\n'
            + b"Content-Type: application/pdf\r\n\r\n" + data + b"\r\n"
            + ("--" + boundary + "--\r\n").encode())
    req = ur.Request("http://127.0.0.1:8888/upload", data=body,
                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    try:
        return json.loads(ur.urlopen(req, timeout=timeout).read().decode())
    except Exception as e:
        return {"error": str(e)}


up = upload_pdf(TEST_PDF)
paths = [f.get("path") for f in (up.get("files") or []) if f.get("path")]
print(f"上传结果: {len(paths)} 个文件 -> {paths}", flush=True)
t0 = time.time()
r = run("请阅读刚才上传的 PDF，用一句话概括它的主要内容", timeout=240, attachments=paths)
el = time.time() - t0
print(f"[{'PASS' if r['ok'] else 'FAIL'}] upload_qa ({el:.0f}s) tools={sorted(r['tools'])} "
      f"final={r['final'][:200]!r}", flush=True)

# ───────── 2) 子代理 ─────────
print("\n===== 2) 子代理派发与监控 =====", flush=True)
s, carry = _open_sse()
s.settimeout(1.0)
_idle = 0; _t0 = time.time()
while time.time() - _t0 < 25 and _idle < 3:
    try:
        _ch = s.recv(65536)
        if not _ch:
            break
        _, carry = _parse(_ch, carry)
        _idle = 0
    except socket.timeout:
        _idle += 1
posted = post_command("请帮我深入研究：Python asyncio 的基本用法与最佳实践，并用一段话总结核心要点")
print(f"[{'PASS' if posted else 'FAIL'}] subagent_dispatch accepted={posted}", flush=True)

counts = collections.Counter()
markers = set()
start = time.time(); last_evt = time.time()
s.settimeout(2.0)
try:
    while time.time() - start < 360:
        try:
            ch = s.recv(65536)
        except socket.timeout:
            if counts.get("ai_final_reply", 0) >= 1 and (time.time() - last_evt) > 40:
                break
            if (time.time() - last_evt) > 150:
                break
            continue
        except Exception:
            continue
        if not ch:
            break
        evs, carry = _parse(ch, carry)
        for e in evs:
            t = e.get("type", ""); counts[t] += 1
            last_evt = time.time()
            blob = json.dumps(e, ensure_ascii=False).lower()
            for mk in ("subagent", "子代理", "child", "spawn"):
                if mk in blob:
                    markers.add(mk)
finally:
    s.close()
ai = counts.get("ai_final_reply", 0)
tool = counts.get("tool_start", 0) + counts.get("tool_end", 0)
print(f"[{'PASS' if (ai >= 1 and tool >= 1) else 'FAIL'}] subagent_monitor "
      f"ai_final_reply={ai} tool_events={tool} markers={sorted(markers)} "
      f"types={dict(counts)}", flush=True)

# ───────── 3) 元宝未登录提示 ─────────
print("\n===== 3) 元宝未登录：应快速给出干净的登录提示 =====", flush=True)
switch_platform("yuanbao"); time.sleep(3)
t0 = time.time()
r = run("请用 file_write 工具创建文件 yb_probe.txt，内容为「probe」", timeout=120)
el = time.time() - t0
f = r["final"]
clean = ("需要登录" in f) or ("未登录" in f)
no_dump = "Call log" not in f and "Locator.wait_for" not in f
print(f"耗时 {el:.0f}s | 干净提示={clean} | 无Playwright堆栈={no_dump}")
print(f"final={f[:220]!r}", flush=True)
print(f"[{'PASS' if (clean and no_dump and el < 60) else 'FAIL'}] yuanbao_login_hint", flush=True)

# 第二条：验证短路（不应再等超时）
t0 = time.time()
r2 = run("再试一次：用 file_write 创建 yb_probe2.txt", timeout=90)
el2 = time.time() - t0
clean2 = ("需要登录" in r2["final"]) or ("未登录" in r2["final"])
print(f"第二条耗时 {el2:.0f}s 干净提示={clean2} final={r2['final'][:120]!r}", flush=True)
print(f"[{'PASS' if (clean2 and el2 < 45) else 'FAIL'}] yuanbao_login_shortcircuit", flush=True)

switch_platform("deepseek"); time.sleep(2)
print(f"\n最终 DeepSeek 实例数 = {deepseek_count()}（基线 {BASE_DS}）", flush=True)
print("health:", health(), flush=True)
