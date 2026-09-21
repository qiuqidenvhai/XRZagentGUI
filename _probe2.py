#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针2：验证 /platform 端点切换 + 各平台对话是否真实响应 + DeepSeek 实例数。"""
import json, time, urllib.request, http.client
import psutil

B = "http://127.0.0.1:8888"
HOST, PORT = "127.0.0.1", 8888
DEEPSEEK_MARK = r"browser_profiles\deepseek"

def health():
    try:
        return json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
    except Exception as e:
        return {"error": str(e)}

def get_platforms():
    try:
        return urllib.request.urlopen(B + "/platforms", timeout=5).read().decode()[:400]
    except Exception as e:
        return f"ERR:{e}"

def switch(key):
    try:
        req = urllib.request.Request(B + "/platform?key=" + key, method="GET")
        return urllib.request.urlopen(req, timeout=10).read().decode()[:200]
    except Exception as e:
        return f"ERR:{e}"

def deepseek_instances():
    dirs = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            args = p.info.get("cmdline") or []
        except Exception:
            continue
        for arg in args:
            if DEEPSEEK_MARK in arg and "--user-data-dir" in arg:
                dirs.add(arg)
    return len(dirs), sorted(dirs)

def sse(timeout=70):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout + 30)
    conn.request("GET", "/events"); resp = conn.getresponse()
    replies = []; start = time.time(); fs = 0.0
    try:
        while time.time() - start < timeout:
            line = resp.readline().decode("utf-8", "replace").rstrip("\n")
            if not line.startswith("data: "): continue
            try: e = json.loads(line[6:])
            except Exception: continue
            if e.get("type") == "ai_final_reply":
                replies.append(e.get("data", {}).get("text", "")); fs = time.time()
            if fs and time.time() - fs > 8: break
    finally:
        conn.close()
    return replies[-1] if replies else ""

log = {}
log["platforms_endpoint"] = get_platforms()
ni0, d0 = deepseek_instances(); log["baseline_deepseek_instances"] = ni0
print("基线 DeepSeek 实例数:", ni0, d0, flush=True)

for key in ["tongyi", "doubao", "yuanbao", "deepseek"]:
    r = switch(key); time.sleep(3)
    h = health()
    ni, d = deepseek_instances()
    print(f"switch {key}: resp={r[:80]!r} platform={h.get('platform')} deepseek_instances={ni}", flush=True)
    log[f"switch_{key}"] = {"resp": r[:120], "platform": h.get("platform"), "deepseek_instances": ni}
    # 发一句问候，看是否真实响应
    req = urllib.request.Request(B + "/command", data=json.dumps({"command": "你好，请用一句话回复即可"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try: urllib.request.urlopen(req, timeout=15)
    except Exception: pass
    time.sleep(1)
    final = sse(70)
    ok = bool(final) and "错误" not in final[:20]
    print(f"  -> chat reply ok={ok}: {final[:80]!r}", flush=True)
    log[f"chat_{key}"] = {"ok": ok, "reply": final[:100]}
    ni2, _ = deepseek_instances()
    log[f"after_chat_{key}_deepseek_instances"] = ni2

with open(r"D:\软件\XianRenZhangAgent\_probe2.json", "w", encoding="utf-8") as f:
    json.dump(log, f, ensure_ascii=False, indent=2)
print("探针2完成 -> _probe2.json", flush=True)
