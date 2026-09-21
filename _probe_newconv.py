# -*- coding: utf-8 -*-
"""真机实测「新建对话」后端侧：看 /command 是否受理 + SSE 是否收到 new_conversation 事件。"""
import json, time, urllib.request, http.client

B = "http://127.0.0.1:8888"
HOST, PORT = "127.0.0.1", 8888
log = {}

# 1) health
try:
    log["health"] = json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
except Exception as e:
    log["health"] = {"error": str(e)}

# 2) 打开 SSE 拿事件流（后台线程式：先连，边发命令边读）
import threading, queue
evq = queue.Queue()
stop = {"s": False}

def sse_reader():
    c = http.client.HTTPConnection(HOST, PORT, timeout=30)
    c.request("GET", "/events")
    r = c.getresponse()
    buf = ""
    try:
        while not stop["s"]:
            line = r.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("data: "):
                buf = line[6:]
                try:
                    e = json.loads(buf)
                    evq.put((e.get("type"), e.get("data")))
                except Exception:
                    evq.put(("_raw", buf[:120]))
    except Exception as e:
        evq.put(("_err", str(e)))
    finally:
        try: c.close()
        except Exception: pass

t = threading.Thread(target=sse_reader, daemon=True)
t.start()
time.sleep(1.0)

# 3) 发「新对话」命令，看受理响应
req = urllib.request.Request(B + "/command",
    data=json.dumps({"command": "新对话"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    log["command_newconv"] = resp
except Exception as e:
    log["command_newconv"] = {"error": str(e)}

# 4) 收 20 秒事件，看有没有 new_conversation / start_new_conversation / 相关日志
seen = []
deadline = time.time() + 20
while time.time() < deadline:
    try:
        typ, data = evq.get(timeout=2)
        seen.append((typ, json.dumps(data, ensure_ascii=False)[:140]))
        # 命中关键事件就早停
        s = str(typ) + " " + json.dumps(data, ensure_ascii=False)
        if any(k in s for k in ("new_conversation", "新对话", "start_new", "conversation", "reset")):
            pass
    except queue.Empty:
        continue
log["sse_events_20s"] = seen[-25:]
stop["s"] = True
t.join(timeout=3)

# 5) 顺带列 /conversations 看当前对话数
try:
    cv = json.loads(urllib.request.urlopen(B + "/conversations", timeout=8).read().decode())
    log["conversations"] = cv if isinstance(cv, list) else cv
except Exception as e:
    log["conversations"] = {"error": str(e)}

print(json.dumps(log, ensure_ascii=False, indent=1))
