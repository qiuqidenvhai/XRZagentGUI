# -*- coding: utf-8 -*-
import json, urllib.request, os
BR = "http://127.0.0.1:9333"
SHOT = r"D:\软件\XianRenZhangAgent\_gui_subagent_real.png"

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

# find the REAL card (contains SUBAGENTTEST)
js = (
    "Array.from(document.querySelectorAll('.subagent-block')).map(function(b,i){"
    "  return {i:i, q:(b.querySelector('.sa-q')||{}).textContent||'', "
    "         status:(b.querySelector('.sa-status')||{}).textContent||'', "
    "         cls:b.className, lines:(b.querySelector('.sa-body')||{}).innerText||''};"
    "})"
)
cards = be(js)
print("num cards:", len(cards))
for c in cards:
    q = c.get("q", "") or ""
    if "SUBAGENTTEST" in q:
        print("=== REAL SUBAGENT CARD (idx %s) ===" % c.get("i"))
        print("  query:", q)
        print("  status:", c.get("status"))
        print("  class:", c.get("cls"))
        print("  body lines:", c.get("lines", "")[:400])
print("shot:", shot(SHOT))
print("screenshot exists:", os.path.exists(SHOT))
