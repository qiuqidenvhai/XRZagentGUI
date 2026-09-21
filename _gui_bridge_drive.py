# -*- coding: utf-8 -*-
"""真机驱动桌面壳测试桥（XRZ_GUI_BRIDGE=1）：
起壳 → 截图（看图标/界面）→ 触发 newConversation() → 抓界面+后端 SSE 反应。
沙箱必须 QTWEBENGINE_DISABLE_SANDBOX=1 + 渲染 flags。"""
import os, sys, json, time, subprocess, urllib.request, socket, http.client

BASE = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\pythonw.exe"
SHOT = os.path.join(BASE, "_gui_drive_shot.png")

env = dict(os.environ)
env["XRZ_GUI_BRIDGE"] = "1"
env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(BASE, "xrz_data", "playwright_browsers")
env["XRZ_DATA_DIR"] = os.path.join(BASE, "xrz_data")

log = {"steps": []}

def step(k, v):
    log["steps"].append(f"{k}: {v}")
    print(k, "=", repr(v)[:160], flush=True)

proc = subprocess.Popen([PY, os.path.join(BASE, "desktop_app.py")],
                        env=env, cwd=BASE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
step("desktop_app_pid", proc.pid)

up = False
for _ in range(40):
    s = socket.socket(); s.settimeout(1)
    try:
        up = s.connect_ex(("127.0.0.1", 9333)) == 0
        s.close()
        if up: break
    except Exception:
        pass
    time.sleep(0.5)
step("bridge_up", up)
if not up:
    proc.kill()
    print(json.dumps(log, ensure_ascii=False, indent=1)); sys.exit(1)

def bridge(js, kind="eval", path=None, timeout=35):
    body = {"js": js, "kind": kind}
    if path: body["path"] = path
    req = urllib.request.Request("http://127.0.0.1:9333", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except Exception as e:
        return {"value": f"ERR {e}"}

ready = False
for _ in range(60):
    r = bridge("(()=>{return (typeof TASKS!=='undefined')?'init':'notinit';})()")
    if r.get("value") == "init":
        ready = True; step("panel_state", "initialized"); break
    time.sleep(1)
step("panel_ready", ready)

r = bridge("(()=>{return JSON.stringify({hasNewConv:typeof newConversation==='function',"
           "hasReset:typeof resetChatView==='function',hasOnboard:typeof showOnboarding==='function',"
           "msgLen:(document.getElementById('messages')||{}).innerHTML?.length});})()")
step("frontend_fns", r.get("value"))

shot = bridge("", "shot", path=SHOT, timeout=30)
step("screenshot_saved", shot.get("value"))

time.sleep(1)
r = bridge("(()=>{try{newConversation();return 'fired';}catch(e){return 'ERR '+e.message;}})()")
step("newConversation_fired", r.get("value"))

time.sleep(8)
r = bridge("(()=>{const m=document.getElementById('messages');return m?m.innerText.slice(0,500):'no#messages';})()")
step("messages_after_newconv", r.get("value"))

c = http.client.HTTPConnection("127.0.0.1", 8888, timeout=12)
c.request("GET", "/events"); resp = c.getresponse()
sse = []
t0 = time.time()
try:
    while time.time() - t0 < 6:
        line = resp.readline().decode("utf-8", "replace").rstrip("\n")
        if line.startswith("data: "):
            try:
                e = json.loads(line[6:]); d = e.get("data", {})
                if e.get("type") in ("ai_final_reply", "task_started"):
                    txt = (d.get("text") or "") if isinstance(d, dict) else json.dumps(d, ensure_ascii=False)
                    sse.append((e.get("type"), (txt or "")[:90]))
            except Exception:
                pass
except Exception:
    pass
c.close()
step("sse_events_during", sse[-12:])

with open(os.path.join(BASE, "_gui_bridge_drive.json"), "w", encoding="utf-8") as f:
    json.dump(log, f, ensure_ascii=False, indent=1)
step("done", "saved")

proc.terminate()
try:
    proc.wait(timeout=8)
except Exception:
    proc.kill()
print(json.dumps(log, ensure_ascii=False, indent=1))
