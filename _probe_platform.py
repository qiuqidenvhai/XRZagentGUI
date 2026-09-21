#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：用正确的 POST /platform 逐个切换，检查后端存活 + 是否双开 DeepSeek。"""
import json, time, re, urllib.request, urllib.error
import psutil

B = "http://127.0.0.1:8888"

def post_platform(key, timeout=120):
    body = json.dumps({"platform": key}).encode()
    req = urllib.request.Request(B + "/platform", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout).read().decode()[:400]
        return f"OK({time.time()-t0:.0f}s) {resp}"
    except Exception as e:
        return f"ERR({time.time()-t0:.0f}s) {e}"

def health():
    try: return json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
    except Exception as e: return {"error": str(e)[:60]}

def dirs():
    paths = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        if p.info.get("name") not in ("chrome.exe", "headless_shell.exe"): continue
        for a in p.info.get("cmdline") or []:
            m = re.search(r"--user-data-dir=(.+)", a)
            if not m: continue
            d = re.sub(r"^--monitor-self-argument=", "", m.group(1).strip().strip('"'))
            paths[d] = paths.get(d, 0) + 1
    return paths

def snap(tag):
    h = health(); d = dirs()
    ds = [k for k in d if "deepseek" in k.lower()]
    print(f"[{tag}] platform={h.get('platform')} alive={'ok' if h.get('status')=='ok' else h.get('error')}", flush=True)
    for k, v in sorted(d.items()):
        print(f"      {v:3d}  {k}", flush=True)
    print(f"      >>> DeepSeek 目录数={len(ds)}  全部目录数={len(d)}", flush=True)
    return h, d, len(ds)

h0, d0, ds0 = snap("baseline")

for key in ["tongyi", "deepseek"]:
    print(f"\n===== POST /platform platform={key} =====", flush=True)
    r = post_platform(key)
    print("   resp:", r, flush=True)
    time.sleep(5)
    h, d, ds = snap(f"after_{key}")
    if h.get("status") != "ok":
        print(f"   ⚠️ 后端在切到 {key} 后无响应！", flush=True)
        break
    if ds > ds0:
        print(f"   ⚠️⚠️ DeepSeek 浏览器目录数 {ds0} -> {ds}：出现第二个 DeepSeek 浏览器！", flush=True)

print("\n探针结束", flush=True)
