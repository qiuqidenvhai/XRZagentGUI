#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速探针：验证「切换到 X 平台」、DeepSeek 浏览器进程数、DeepSeek 是否响应。"""
import json, time, urllib.request, http.client
import psutil

B = "http://127.0.0.1:8888"
HOST, PORT = "127.0.0.1", 8888
DEEPSEEK_MARK = r"browser_profiles\deepseek"  # cmdline 中子串（Windows 反斜杠）

def health():
    try:
        return json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
    except Exception as e:
        return {"error": str(e)}

def post(cmd):
    req = urllib.request.Request(B + "/command",
        data=json.dumps({"command": cmd}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode()).get("type")
    except Exception as e:
        return f"ERR:{e}"

def deepseek_browser_count():
    n = 0; names = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if DEEPSEEK_MARK in cl:
            n += 1; names.append(p.info.get("name"))
    return n, names

def sse(timeout=90):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout + 30)
    conn.request("GET", "/events")
    resp = conn.getresponse()
    replies = []; start = time.time(); final_seen = 0.0
    try:
        while time.time() - start < timeout:
            line = resp.readline().decode("utf-8", "replace").rstrip("\n")
            if not line.startswith("data: "):
                continue
            try: e = json.loads(line[6:])
            except Exception: continue
            if e.get("type") == "ai_final_reply":
                replies.append(e.get("data", {}).get("text", "")); final_seen = time.time()
            if final_seen and time.time() - final_seen > 8:
                break
    finally:
        conn.close()
    return replies[-1] if replies else ""

log = {}
print("基线 deepseek 浏览器进程数:", deepseek_browser_count(), flush=True)
log["baseline_health"] = health()
n0, _ = deepseek_browser_count(); log["baseline_deepseek_count"] = n0

for p in ["tongyi", "doubao", "yuanbao", "deepseek"]:
    t = post(f"切换到 {p} 平台")
    time.sleep(3)
    h = health()
    n, names = deepseek_browser_count()
    print(f"切换到 {p}: post={t} health.platform={h.get('platform')} deepseek_count={n} {names}", flush=True)
    log[f"switch_{p}"] = {"post": t, "platform": h.get("platform"), "deepseek_count": n}

# DeepSeek 响应测试
post("请用一句话解释什么是 Python 装饰器"); time.sleep(1)
final = sse(90)
log["deepseek_chat_reply"] = final[:120]
log["deepseek_chat_ok"] = bool(final) and "错误" not in final[:20]
n1, _ = deepseek_browser_count()
log["after_deepseek_count"] = n1
print("DeepSeek 回复:", final[:120], flush=True)
print("终态 deepseek 浏览器进程数:", deepseek_browser_count(), flush=True)

with open(r"D:\软件\XianRenZhangAgent\_probe.json", "w", encoding="utf-8") as f:
    json.dump(log, f, ensure_ascii=False, indent=2)
print("探针完成 -> _probe.json", flush=True)
