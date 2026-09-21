# -*- coding: utf-8 -*-
"""新建对话 = 独立上下文 端到端测试：
   1) 发一条旧任务相关的问题（确认当前上下文属于旧任务）
   2) 发 `新对话` 命令
   3) 发一条全新问题
   4) 确认它进入了**新的独立会话文件**（新 task_id、新 conv 文件），且不与旧任务合并
"""
import urllib.request, json, http.client, time, threading, queue, os, glob

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


CONV_DIR = r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\conversations"


def newest_conv_mtime():
    files = glob.glob(os.path.join(CONV_DIR, "conv_*.json"))
    if not files:
        return 0, None
    best = max(files, key=os.path.getmtime)
    return os.path.getmtime(best), best


out = []
out.append("=== STEP 0: baseline newest conv ===")
bt0, bf0 = newest_conv_mtime()
out.append("before newconv: %s" % bf0)

# 1) new conversation command
drain()
r = post("新对话")
out.append("新对话 post -> %s" % r)
time.sleep(4)

# 2) send a fresh, unrelated question in the NEW context
drain()
r2 = post("完全全新的问题：10 乘以 10 等于多少？只回答数字。")
out.append("fresh-q post -> %s" % r2)
e, f = wait_final(90)
out.append("fresh-q saw=%s" % (e[:25],))
out.append("fresh-q FINAL=%s" % (json.dumps(f, ensure_ascii=False)[:300] if f else "NONE"))

time.sleep(3)
out.append("=== STEP 1: after newconv+fresh ===")
bt1, bf1 = newest_conv_mtime()
out.append("newest conv now: %s (mtime %d)" % (bf1, bt1))
out.append("new-conv-created=%s" % ("YES" if bf1 != bf0 else "NO (same file)"))
if bf1 != bf0 and bf1:
    try:
        c = json.load(open(bf1, encoding="utf-8"))
        users = [m.get("content", "") for m in c.get("messages", []) if m.get("role") == "user"]
        out.append("new conv user msgs=%r" % users)
    except Exception as ex:
        out.append("read new conv err %r" % ex)
open(r"D:\软件\XianRenZhangAgent\_newconv.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
