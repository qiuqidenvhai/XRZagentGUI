# -*- coding: utf-8 -*-
"""最终验收（更新后的 DeepSeek 选择器）：
  1) 发一条新问题 → 收到正确 AI 回复（验证 send_selector='' 走 Enter + response_selector）
  2) 发 `新对话` → 再发一条 → 验证是否真正开了独立会话（新 task / 新 conv 文件）
"""
import urllib.request, json, http.client, time, threading, queue, os, glob

BASE = "127.0.0.1:8888"
CONV_DIR = r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\conversations"


def post(cmd):
    req = urllib.request.Request("http://" + BASE + "/command",
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


def wait_final(timeout=90, tag=""):
    drain()
    evs, final, task_ids = [], None, []
    start = time.time()
    while time.time() - start < timeout:
        try:
            ev = q.get(timeout=2)
        except queue.Empty:
            continue
        t = ev.get("type", "?")
        evs.append(t)
        if t == "task_started":
            task_ids.append((ev.get("data") or {}).get("task_id"))
        if t == "ai_final_reply":
            final = ev.get("data")
            break
        if t == "_sse_err":
            evs.append("ERR " + ev["data"])
            break
    return evs, final, task_ids


def drain():
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


threading.Thread(target=pump, daemon=True).start()
time.sleep(1)
drain()

out = []


def newest_conv():
    files = glob.glob(os.path.join(CONV_DIR, "conv_*.json"))
    if not files:
        return 0, None
    best = max(files, key=os.path.getmtime)
    return os.path.getmtime(best), best


bt0, bf0 = newest_conv()
out.append("baseline newest conv=%s" % bf0)

# 1) fresh question
drain()
r1 = post("用一句话告诉我：一年有多少个星期？只回答数字。")
out.append("Q1 post -> %s" % r1)
e1, f1, t1 = wait_final(90, "q1")
out.append("Q1 saw=%s task_ids=%s" % (e1[:15], t1[:3]))
out.append("Q1 FINAL=%s" % (json.dumps(f1, ensure_ascii=False)[:200] if f1 else "NONE"))

# 2) new conversation + fresh
out.append("--- new-conversation independence ---")
newconv_task = None
drain()
r2 = post("新对话")
out.append("新对话 post -> %s" % r2)
time.sleep(4)
drain()
r3 = post("全新问题：7 加 3 等于多少？只回答数字。")
out.append("newconv-followup post -> %s" % r3)
e3, f3, t3 = wait_final(90, "q3")
out.append("q3 saw=%s task_ids=%s" % (e3[:15], t3[:3]))
out.append("q3 FINAL=%s" % (json.dumps(f3, ensure_ascii=False)[:200] if f3 else "NONE"))

time.sleep(3)
bt1, bf1 = newest_conv()
out.append("after: newest conv=%s mtime_before=%s mtime_after=%s" % (bf1, bt0, bt1))
out.append("independent-new-conversation-created=%s" % ("YES" if bf1 != bf0 else "NO"))

open(r"D:\软件\XianRenZhangAgent\_final.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
