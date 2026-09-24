# -*- coding: utf-8 -*-
import json, urllib.request, os
BR = "http://127.0.0.1:9333"
SHOT = r"D:\软件\XianRenZhangAgent\_gui_subagent_shot.png"

def be(js):
    req = urllib.request.Request(BR + "/eval",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=15).read().decode()

# returns JSON string of array -> parse it
js = ("JSON.stringify(Array.from(document.querySelectorAll('.subagent-block')).map(function(b){"
      "  return {q:(b.querySelector('.sa-q')||{}).textContent||'', "
      "         status:(b.querySelector('.sa-status')||{}).textContent||'', "
      "         cls:b.className, lines:(b.querySelector('.sa-body')||{}).innerText||''};"
      "}))")
raw = be(js)
print("RAW:", raw[:300])
try:
    cards = json.loads(raw)
except Exception as e:
    print("parse err:", e); cards = []
print("num cards:", len(cards))
for i, c in enumerate(cards):
    q = c.get("q", "") or ""
    print("--- card", i, "---")
    print("  q:", q[:90])
    print("  status:", c.get("status"))
    print("  cls:", c.get("cls"))
    print("  lines:", (c.get("lines", "") or "")[:220])

# 截图当前 GUI
req = urllib.request.Request(BR + "/shot",
    data=json.dumps({"kind": "shot", "path": SHOT}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
print("shot:", urllib.request.urlopen(req, timeout=40).read().decode())
print("screenshot exists:", os.path.exists(SHOT))
