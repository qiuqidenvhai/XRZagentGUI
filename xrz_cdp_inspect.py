"""browser 级 CDP：找到 DeepSeek 页面并 attach，抓 DOM + 截图。"""
import json, websocket, base64, time, urllib.request

BWS = "ws://127.0.0.1:9222/devtools/browser"
b = websocket.create_connection(BWS, timeout=20)
_bid = 0
def bcdp(method, params=None, timeout=15):
    global _bid
    _bid += 1
    b.send(json.dumps({"id": _bid, "method": method, "params": params or {}}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = b.recv()
        d = json.loads(raw)
        if d.get("id") == _bid:
            return d
    return None

# 列出 targets，找 deepseek page
r = bcdp("Target.getTargets")
targets = (r.get("result", {}).get("targetInfos") or []) if r else []
ds = None
for t in targets:
    if t.get("type") == "page" and "deepseek" in (t.get("url") or ""):
        ds = t; break
if not ds:
    for t in targets:
        if t.get("type") == "page":
            ds = t; break
print("选中的 target:", ds.get("targetId"), ds.get("url") if ds else None)
if not ds:
    print("无 page target"); raise SystemExit(1)

# attach
ar = bcdp("Target.attachToTarget", {"targetId": ds["targetId"], "flatten": True})
sess = ar.get("result", {}).get("sessionId")
print("sessionId:", sess)

_sid = 0
def scdp(method, params=None, timeout=15):
    global _sid
    _sid += 1
    b.send(json.dumps({"id": _sid, "method": method, "params": params or {},
                       "sessionId": sess}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = b.recv()
        d = json.loads(raw)
        if d.get("id") == _sid:
            return d
    return None

scdp("Page.enable")
scdp("Runtime.enable")
time.sleep(0.5)

# 截图
sr = scdp("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
if sr and "result" in sr and "data" in sr["result"]:
    with open("xrz_deepseek_live.png", "wb") as f:
        f.write(base64.b64decode(sr["result"]["data"]))
    print("截图 -> xrz_deepseek_live.png")

expr = """() => {
  const out = {};
  out.url = location.href; out.title = document.title;
  out.bodyText = (document.body && document.body.innerText || '').slice(0, 700);
  const lb = Array.from(document.querySelectorAll('button, a')).filter(e => /登录|Login|Sign in|注册/.test(e.innerText||''));
  out.loginButtons = lb.slice(0,6).map(e => (e.innerText||'').trim().slice(0,30));
  out.hasTextarea = !!document.querySelector('textarea');
  out.markdownCount = document.querySelectorAll('.markdown-body, .prose, [class*=message], [class*=markdown], [data-message-id], .ds-markdown, .message-content').length;
  out.cookie = document.cookie.split(';').map(c=>c.split('=')[0].trim()).filter(Boolean);
  return out;
}"""
er = scdp("Runtime.evaluate", {"expression": expr, "returnByValue": True})
if er and "result" in er:
    print(json.dumps(er["result"].get("result", {}).get("value"), ensure_ascii=False, indent=2))
else:
    print("evaluate 失败:", er)
b.close()
