# -*- coding: utf-8 -*-
"""yuanbao 端到端轻量验证（可达平台补收发能力覆盖）。
切 yuanbao → /command 发「回复OK」 → SSE 读 ai_final_reply（≤120s）。
掉登录/未响应会如实记录。最后切回 deepseek 稳态。"""
import json, time, urllib.request

def post(path, body, timeout=90):
    req = urllib.request.Request("http://127.0.0.1:8888" + path,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

def sse_read(timeout_s=120):
    """订阅 /events，读到 ai_final_reply 或 error 或登录墙即返回。"""
    import queue, threading
    req = urllib.request.Request("http://127.0.0.1:8888/events", headers={"Accept": "text/event-stream"})
    resp = urllib.request.urlopen(req, timeout=timeout_s + 10)
    deadline = time.time() + timeout_s
    result = {}
    for raw in resp:
        if time.time() > deadline:
            break
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        data = ev.get("data", ev)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass
        etype = ev.get("type") or (data.get("type") if isinstance(data, dict) else None)
        if etype == "ai_final_reply":
            result["final"] = (data or {}).get("text", "") if isinstance(data, dict) else str(data)
            break
        if etype == "error":
            result["error"] = (data or {}).get("text", "") if isinstance(data, dict) else str(data)
            break
        if isinstance(data, dict) and data.get("type") == "error":
            result["error"] = data.get("text", "")
            break
        # 登录墙/未登录提示
        txt = json.dumps(ev, ensure_ascii=False)
        if ("尚未登录" in txt or "需要登录" in txt or "需要手动过验证" in txt or "CAPTCHA" in txt) and etype in ("ai_final_reply", "error", "system"):
            result["login_wall"] = txt[:200]
    return result

print("=== yuanbao e2e ===", flush=True)
post("/platform", {"platform": "yuanbao"})
time.sleep(3.0)
try:
    c = post("/command", {"command": "请回复两个字：OK"}, timeout=90)
    print("command:", json.dumps(c, ensure_ascii=False)[:150], flush=True)
except Exception as e:
    print("send 失败:", e, flush=True)

res = sse_read(timeout_s=120)
print("SSE result:", json.dumps(res, ensure_ascii=False)[:600], flush=True)

verdict = "FAIL"
if "final" in res and res.get("final"):
    verdict = "PASS-yuanbao正常回复"
elif res.get("login_wall") or res.get("error"):
    verdict = "未登录/风控" + ("" if res.get("login_wall") else "")
elif res:
    verdict = "超时-未收到回复"

print(f">>> yuanbao verdict: {verdict}", flush=True)

# 恢复 deepseek 稳态
try:
    print("back:", post("/platform", {"platform": "deepseek"}).get("text"), flush=True)
except Exception as e:
    print("restore err:", e, flush=True)
