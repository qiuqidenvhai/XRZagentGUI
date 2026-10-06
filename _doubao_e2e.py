# -*- coding: utf-8 -*-
"""豆包端到端登录态核验（用户确认豆包是登录的，上轮静态提示误判）。
切 doubao → /command 发「请回复两个字：OK」→ SSE 读 ai_final_reply。
拿到正常回复 = 登录态在；秒回 [需要手动过验证] = 撞 CAPTCHA；登录墙 = 掉登录。
单次轻量消息，不发多轮避免风控升级。用完切回 deepseek 稳态。"""
import json, time, urllib.request

def post(path, body, timeout=90):
    req = urllib.request.Request("http://127.0.0.1:8888" + path,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

def sse_read(timeout_s=150):
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
    return result

print("=== doubao e2e ===", flush=True)
post("/platform", {"platform": "doubao"})
time.sleep(4.0)
try:
    c = post("/command", {"command": "请回复两个字：OK"}, timeout=90)
    print("command:", json.dumps(c, ensure_ascii=False)[:150], flush=True)
except Exception as e:
    print("send 失败:", e, flush=True)

res = sse_read(timeout_s=150)
print("SSE result:", json.dumps(res, ensure_ascii=False)[:500], flush=True)

final = res.get("final", "")
verdict = "未知"
if "手动过验证" in final or "CAPTCHA" in final or "验证" in final:
    verdict = "撞CAPTCHA-风控墙（登录态本身可能在，但自动操作触发验证）"
elif res.get("error") and ("登录" in res.get("error") or "尚未" in res.get("error")):
    verdict = "掉登录"
elif final:
    verdict = "PASS-豆包正常回复（登录态在）"
elif not res:
    verdict = "超时-未收到回复"

print(f">>> doubao verdict: {verdict}", flush=True)

try:
    print("back:", post("/platform", {"platform": "deepseek"}).get("text"), flush=True)
except Exception as e:
    print("restore err:", e, flush=True)
