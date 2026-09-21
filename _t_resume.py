# -*- coding: utf-8 -*-
"""切回旧任务并继续聊 端到端测试（走后端 /command + /events SSE）。"""
import urllib.request, json, http.client, time, threading, queue

BASE = "127.0.0.1:8888"


def post(cmd):
    req = urllib.request.Request(
        "http://" + BASE + "/command",
        data=json.dumps({"command": cmd}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=30))


q = queue.Queue()


def pump():
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8888, timeout=600)
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
old = "deepseek_20260920_083831_44741"
drain()

# 1) 恢复旧任务
r1 = post("恢复对话 " + old)
out.append("resume post -> %s" % r1)
time.sleep(3)
e1, f1 = wait_final(90)
out.append("resume saw=%s" % (e1[:25],))
out.append("resume FINAL=%s" % (json.dumps(f1, ensure_ascii=False)[:400] if f1 else "NONE"))

# 2) 在还原的上下文里继续追问
r2 = post("接着刚才那个任务，用一句话告诉我：它当时要做什么？只回答它要做什么的事。")
out.append("followup post -> %s" % r2)
time.sleep(3)
e2, f2 = wait_final(90)
out.append("followup saw=%s" % (e2[:25],))
out.append("followup FINAL=%s" % (json.dumps(f2, ensure_ascii=False)[:400] if f2 else "NONE"))

open(r"D:\软件\XianRenZhangAgent\_resume.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
