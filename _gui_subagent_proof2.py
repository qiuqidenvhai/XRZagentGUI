# -*- coding: utf-8 -*-
"""真实 GUI 子代理渲染证明 v2：用唯一 token 隔离【本轮】产生的子代理卡片，
轮询真实 GUI DOM，确认新卡片出现并带 LIVE 进度（start -> log -> done）。
"""
import json, time, urllib.request, os, sys

API = "http://127.0.0.1:8888"
BR = "http://127.0.0.1:9333"
TOKEN = "SUBPROOF_%d" % int(time.time())
SHOT = r"D:\软件\XianRenZhangAgent\_gui_subagent_proof2.png"


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
    print("GUI title:", be("document.title"), flush=True)
    # 记录已有的子代理卡片 query（stale 过滤用）
    before = be("Array.from(document.querySelectorAll('.subagent-block .sa-q')).map(e=>e.textContent)")
    print("existing cards queries:", before, flush=True)

    # 触发子代理任务（query 里塞唯一 token，便于在 GUI 里定位本轮卡片）
    msg = ("你必须使用 browser_research 子代理工具去浏览器里调研，禁止用你自己的知识直接回答。"
           "请用子代理打开网页搜索“%s 仙人掌 Agent 是啥”，把查到的信息简要告诉我。" % TOKEN)
    r = urllib.request.urlopen(
        urllib.request.Request(API + "/command", data=json.dumps({"command": msg}).encode(),
                              headers={"Content-Type": "application/json"}, method="POST"), timeout=10)
    print("command:", r.read().decode(), flush=True)

    # 轮询：找包含 TOKEN 的新卡片
    found = False
    new_id = -1
    first_snippet = ""
    done_snippet = ""
    for i in range(90):  # 最多 7.5 分钟
        time.sleep(5)
        # 找到包含 TOKEN 的卡片索引
        idx = be("""(function(){
          var bs=document.querySelectorAll('.subagent-block');
          for(var i=0;i<bs.length;i++){
            var q=(bs[i].querySelector('.sa-q')||{}).textContent||'';
            if(q.indexOf('%s')>=0) return i;
          }
          return -1;
        })()""" % TOKEN)
        if isinstance(idx, (int, float)) and idx >= 0:
            txt = be("(document.querySelectorAll('.subagent-block')[%d]).innerText" % int(idx))
            cls = be("(document.querySelectorAll('.subagent-block')[%d]).className" % int(idx))
            if not found:
                found = True
                first_snippet = txt
                print(">>> NEW subagent card idx=%s at %ds" % (idx, i * 5), flush=True)
                print("    first text:", txt[:200], flush=True)
            if "done" in (cls or ""):
                done_snippet = txt
                print(">>> card DONE at %ds" % (i * 5), flush=True)
                print("    done text:", txt[:300], flush=True)
                break
        if i % 6 == 0:
            print("  poll %ds idx=%s" % (i * 5, idx), flush=True)

    try:
        print("shot:", shot(SHOT), flush=True)
    except Exception as e:
        print("shot err:", e, flush=True)

    html = be("""(function(){
      var bs=document.querySelectorAll('.subagent-block');
      for(var i=0;i<bs.length;i++){
        var q=(bs[i].querySelector('.sa-q')||{}).textContent||'';
        if(q.indexOf('%s')>=0) return bs[i].outerHTML.slice(0,1000);
      }
      return 'NONE';
    })()""" % TOKEN)
    print("\n==== RESULT ====", flush=True)
    print("token:", TOKEN, flush=True)
    print("new_subagent_card_rendered:", found, flush=True)
    print("card_first_text:", (first_snippet or "")[:500], flush=True)
    print("card_done_text:", (done_snippet or "")[:500], flush=True)
    print("card_html_head:", (html or "")[:500], flush=True)
    print("screenshot:", os.path.abspath(SHOT) if os.path.exists(SHOT) else "MISSING", flush=True)

    open(r"D:\软件\XianRenZhangAgent\_gui_subagent_proof2.json", "w", encoding="utf-8").write(
        json.dumps({"token": TOKEN, "new_subagent_card_rendered": found,
                    "card_first_text": first_snippet, "card_done_text": done_snippet,
                    "screenshot": os.path.abspath(SHOT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
