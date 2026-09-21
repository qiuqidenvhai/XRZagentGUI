# -*- coding: utf-8 -*-
"""给 DeepSeek 发一条消息 → 等回复 → 再 dump 一次 live DOM（此时页面含真实回复气泡），
用于核对新版 DeepSeek 的消息容器 class（response_selector）。"""
import urllib.request, json, http.client, time, threading, queue

BASE = "127.0.0.1:8888"


def post(cmd):
    req = urllib.request.Request("http://" + BASE + "/command",
                                 data=json.dumps({"command": cmd}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=30))


q = queue.Queue()


def pump():
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8888, timeout=300)
        c.request("GET", "/events", headers={"Accept": "text/event-stream"})
        r = c.getresponse()
        while not r.isclosed():
            line = r.readline()
            if not line:
                continue
            line = line.decode("utf-8", "ignore")
            if line.startswith("data:"):
                d = line[5:].strip()
                if d:
                    try:
                        q.put(json.loads(d))
                    except Exception:
                        pass
    except Exception as e:
        q.put({"type": "_sse_err", "data": repr(e)})


threading.Thread(target=pump, daemon=True).start()
time.sleep(1)


def drain():
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def wait_final(timeout=90):
    drain()
    evs, final = [], None
    start = time.time()
    while time.time() - start < timeout:
        try:
            ev = q.get(timeout=2)
        except queue.Empty:
            continue
        evs.append(ev.get("type", "?"))
        if ev.get("type") == "ai_final_reply":
            final = ev.get("data")
            break
        if ev.get("type") == "_sse_err":
            evs.append("ERR " + ev["data"])
            break
    return evs, final


out = []
drain()
r = post("用一句话告诉我：水在零度会发生什么？只回答一句话。")
out.append("post -> %s" % r)
e, f = wait_final(90)
out.append("saw=%s" % (e[:20],))
out.append("FINAL=%s" % (json.dumps(f, ensure_ascii=False)[:200] if f else "NONE"))

# 再 dump live DOM（此时页面有真实回复）
time.sleep(2)
try:
    d = json.load(urllib.request.urlopen("http://127.0.0.1:8888/dom", timeout=40))
    html = d.get("html") or ""
    open(r"D:/软件/XianRenZhangAgent/_deepseek_dom_after.html", "w", encoding="utf-8").write(html)
    out.append("DOM-after len=%d url=%r note=%s" % (len(html), d.get("url"), d.get("note")))
except Exception as ex:
    out.append("dom-after fail %r" % ex)

open(r"D:/软件/XianRenZhangAgent/_after.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
