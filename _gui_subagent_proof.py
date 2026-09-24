# -*- coding: utf-8 -*-
"""真实 GUI 子代理渲染证明：
- 后端 /command 触发 browser_research 子代理（deepseek）
- 通过桌面壳测试桥（127.0.0.1:9333）轮询真实 GUI DOM，确认 .subagent-block 出现并带 subagent_task_id
- 截图留证
"""
import json, time, urllib.request, os

API = "http://127.0.0.1:8888"
BR = "http://127.0.0.1:9333"
SHOT = r"D:\软件\XianRenZhangAgent\_gui_subagent_proof.png"


def be(js):
    req = urllib.request.Request(BR + "/eval",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["value"]


def shot(path):
    req = urllib.request.Request(BR + "/shot",
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=40).read().decode()


def main():
    # 1) 确认 GUI 已加载
    print("GUI title:", be("document.title"), flush=True)
    print("subagent-block before:", be("document.querySelectorAll('.subagent-block').length"), flush=True)

    # 2) 触发子代理任务
    msg = ("你必须使用 browser_research 子代理工具去浏览器里调研，禁止用你自己的知识直接回答。"
           "请用子代理打开网页搜索“仙人掌 Agent 是什么，有哪些核心功能”，把查到的信息简要告诉我。")
    r = urllib.request.urlopen(
        urllib.request.Request(API + "/command", data=json.dumps({"command": msg}).encode(),
                              headers={"Content-Type": "application/json"}, method="POST"), timeout=10)
    print("command:", r.read().decode(), flush=True)

    # 3) 轮询真实 GUI DOM 找子代理卡片
    found = False
    snippet = ""
    first_ts = 0
    for i in range(72):  # 最多 6 分钟
        time.sleep(5)
        n = be("document.querySelectorAll('.subagent-block').length")
        n = n if isinstance(n, (int, float)) else 0
        if n and n > 0:
            snippet = be("(document.querySelector('.subagent-block')||{}).innerText || ''")
            if not found:
                found = True
                first_ts = i * 5
                print(">>> SUBAGENT CARD APPEARED at %ds, blocks=%s" % (i * 5, n), flush=True)
                # 多收集几轮进度再继续
        if found and i * 5 >= first_ts + 20:
            break
        if i % 6 == 0:
            print("  poll %ds blocks=%s" % (i * 5, n), flush=True)

    # 4) 截图
    try:
        print("shot:", shot(SHOT), flush=True)
    except Exception as e:
        print("shot err:", e, flush=True)

    # 5) 也抓一次带 subagent_task_id 的卡片 HTML 片段
    html = be("document.querySelector('.subagent-block') ? document.querySelector('.subagent-block').outerHTML.slice(0,800) : 'NONE'")
    print("\n==== RESULT ====", flush=True)
    print("subagent_card_rendered:", found, flush=True)
    print("card_text_snippet:", (snippet or "")[:600], flush=True)
    print("card_html_head:", (html or "")[:400], flush=True)
    print("screenshot:", os.path.abspath(SHOT) if os.path.exists(SHOT) else "MISSING", flush=True)

    open(r"D:\软件\XianRenZhangAgent\_gui_subagent_proof.json", "w", encoding="utf-8").write(
        json.dumps({"subagent_card_rendered": found, "card_text": snippet, "screenshot": os.path.abspath(SHOT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
