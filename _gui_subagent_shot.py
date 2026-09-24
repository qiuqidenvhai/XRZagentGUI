# -*- coding: utf-8 -*-
"""真实 GUI 子代理卡片截图（紧捕获）：发任务前记录已有卡片，
发任务后轮询，一旦出现【新】卡片立刻截图，避免页面自重载把卡片刷没。
"""
import json, time, urllib.request, os

API = "http://127.0.0.1:8888"
BR = "http://127.0.0.1:9333"
TOKEN = "FINALPROOF_%d" % int(time.time())
SHOT = r"D:\软件\XianRenZhangAgent\_gui_subagent_shot.png"


def be(js):
    req = urllib.request.Request(BR + "/eval",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["value"]


def cards_queries():
    js = ("JSON.stringify(Array.from(document.querySelectorAll('.subagent-block')).map("
          "function(b){return (b.querySelector('.sa-q')||{}).textContent||'';}))")
    try:
        return set(be(js))
    except Exception:
        return set()


def shot(path):
    req = urllib.request.Request(BR + "/shot",
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=40).read().decode()


def main():
    before = cards_queries()
    print("before cards:", before, flush=True)

    msg = ("必须使用 browser_research 子代理工具去浏览器里调研，禁止用你自己的知识直接回答。"
           "请用子代理打开网页搜索“%s 2026 年流行的开源 AI Agent 框架”，把查到的框架名告诉我。" % TOKEN)
    r = urllib.request.urlopen(
        urllib.request.Request(API + "/command", data=json.dumps({"command": msg}).encode(),
                              headers={"Content-Type": "application/json"}, method="POST"), timeout=10)
    print("command:", r.read().decode(), flush=True)

    found = False
    new_q = ""
    for i in range(80):
        time.sleep(3)
        cur = cards_queries()
        new = cur - before
        if new:
            found = True
            new_q = sorted(new)[0]
            print(">>> NEW card at %ds: %s" % (i * 3, new_q[:80]), flush=True)
            try:
                print("shot:", shot(SHOT), flush=True)
            except Exception as e:
                print("shot err:", e, flush=True)
            break
        if i % 8 == 0:
            print("  poll %ds cards=%d" % (i * 3, len(cur)), flush=True)

    print("\n==== RESULT ====", flush=True)
    print("token:", TOKEN, flush=True)
    print("new_subagent_card_rendered:", found, flush=True)
    print("new_card_query:", new_q[:120], flush=True)
    print("screenshot:", os.path.abspath(SHOT) if os.path.exists(SHOT) else "MISSING", flush=True)
    open(r"D:\软件\XianRenZhangAgent\_gui_subagent_shot.json", "w", encoding="utf-8").write(
        json.dumps({"token": TOKEN, "new_subagent_card_rendered": found, "new_card_query": new_q}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
