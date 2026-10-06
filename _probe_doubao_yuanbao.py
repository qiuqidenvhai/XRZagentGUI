# -*- coding: utf-8 -*-
"""doubao/yuanbao 可达性探测（克制版：只切平台读 status 登录态，不发 /command，
避免触发 CAPTCHA 风控 / 产生真实任务。红线：所有平台覆盖，需如实记录状态。"""
import json, time, urllib.request

def post(path, body, timeout=90):
    req = urllib.request.Request("http://127.0.0.1:8888" + path,
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

def get(path, timeout=90):
    return json.loads(urllib.request.urlopen("http://127.0.0.1:8888" + path, timeout=timeout).read().decode())

for p in ("doubao", "yuanbao"):
    print(f"=== {p} ===", flush=True)
    try:
        r = post("/platform", {"platform": p})
        print("switch:", r.get("text"), flush=True)
    except Exception as e:
        print(f"switch 失败（不可达）:", e, flush=True)
        continue
    time.sleep(4.0)
    st = get("/status")
    txt = st.get("text", "")
    print("status:", txt[:400].replace("\n", " | "), flush=True)
    cap = any(k in txt for k in ("CAPTCHA", "风控", "手动过验证", "过验证"))
    nologin = any(k in txt for k in ("未登录", "尚未登录", "需要登录", "掉登录"))
    ready = "已连接" in txt or "就绪" in txt or "运行中" in txt
    verdict = "CAPTCHA-风控墙" if cap else ("掉登录" if nologin else ("可达-已连接" if ready else "未知"))
    print(f">>> {p}: {verdict}", flush=True)

# 恢复到 deepseek（默认稳态平台），避免把用户留在异常平台
try:
    r = post("/platform", {"platform": "deepseek"})
    print("back to deepseek:", r.get("text"), flush=True)
except Exception as e:
    print("restore err:", e, flush=True)
