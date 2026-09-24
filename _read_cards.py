# -*- coding: utf-8 -*-
import json, urllib.request
BR = "http://127.0.0.1:9333"

def be(js):
    req = urllib.request.Request(BR + "/eval",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["value"]

js = (
    "Array.from(document.querySelectorAll('.subagent-block')).map(function(b){"
    "  return {q:(b.querySelector('.sa-q')||{}).textContent||'', "
    "         status:(b.querySelector('.sa-status')||{}).textContent||'', "
    "         cls:b.className, "
    "         lines:(b.querySelector('.sa-body')||{}).innerText||''};"
    "})"
)
cards = be(js)
print("num cards:", len(cards) if isinstance(cards, list) else cards)
if isinstance(cards, list):
    for i, c in enumerate(cards):
        print("--- card", i, "---")
        print("  q:", (c.get("q", "") or "")[:90])
        print("  status:", c.get("status", ""))
        print("  cls:", c.get("cls", ""))
        print("  lines:", (c.get("lines", "") or "")[:220])
