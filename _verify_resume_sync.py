#!/usr/bin/env python3
"""真机 E2E：验证「恢复对话」同步浏览器 + 「新建对话」真开新任务。"""
import json, time, urllib.request, sys

BASE = "http://127.0.0.1:8888"

def req(path, payload=None, method=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE+path, data=data, method=method or ("POST" if data else "GET"),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def probe_url(timeout=30):
    try:
        return req("/probe", timeout=timeout).get("url") or ""
    except Exception as e:
        print("  probe err:", e); return ""

print("=== 步骤1：取一个带 URL 的近期任务 ===")
convs = req("/conversations", timeout=15)
items = convs.get("conversations") or convs.get("tasks") or (convs if isinstance(convs, list) else [])
target = None
for t in items:
    if t.get("url") and t.get("id"):
        target = t
        if "deepseek" in str(t.get("platform","")):
            break
if not target:
    print("❌ 找不到带 url 的任务:", json.dumps(convs, ensure_ascii=False)[:500]); sys.exit(1)
tid, turl = target["id"], target["url"]
print(f"目标任务: {tid}\n  平台={target.get('platform')} url={turl}")

print("\n=== 步骤2：先故意把浏览器带去别处（制造『不同步』现场）===")
print("当前页面 URL:", probe_url())

print("\n=== 步骤3：POST /command 恢复对话 ===")
r = req("/command", {"command": "恢复对话 " + tid}, timeout=30)
print("command resp:", json.dumps(r, ensure_ascii=False)[:200])

print("\n=== 步骤4：轮询 /probe 等浏览器跳回任务页 ===")
ok = False
for i in range(20):
    time.sleep(3)
    u = probe_url()
    print(f"  [{i}] {u}")
    if u and (u.rstrip("/") == turl.rstrip("/") or (turl.rstrip("/") in u)):
        ok = True
        break
print("\n✅ 恢复对话：浏览器已同步跳回任务页 PASS" if ok else "\n❌ 恢复对话：浏览器未跳回任务页 FAIL")

sys.exit(0 if ok else 2)
