# -*- coding: utf-8 -*-
"""读取子代理卡的真实内容与状态（多行 JS 用文件传，避免命令行转义问题）。"""
import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BRIDGE = "http://127.0.0.1:9333/"

JS = r"""
(() => {
  const b = document.querySelector('.subagent-block');
  if (!b) return JSON.stringify({exists: false});
  const r = b.getBoundingClientRect();
  const head = b.querySelector('.sa-head');
  const lines = b.querySelectorAll('.sa-line, .sa-item, .sa-entry, .sa-msg');
  const txt = (e) => (e && e.textContent ? e.textContent.replace(/\s+/g, ' ').trim() : '');
  return JSON.stringify({
    exists: true,
    h: Math.round(r.height),
    w: Math.round(r.width),
    visible: r.height > 0 && r.width > 0,
    inViewport: r.top >= 0 && r.bottom <= (window.innerHeight || 0),
    head: txt(head).slice(0, 80),
    status: txt(b.querySelector('.sa-status')),
    lineCount: lines.length,
    lines: Array.from(lines).slice(0, 6).map(txt).map(s => s.slice(0, 90)),
    collapsed: b.classList.contains('collapsed'),
    headBg: head ? getComputedStyle(head).backgroundColor : null,
  });
})()
"""

req = urllib.request.Request(BRIDGE, data=json.dumps({"kind": "eval", "js": JS}).encode(),
                            headers={"Content-Type": "application/json"}, method="POST")
val = json.loads(_op.open(req, timeout=40).read()).get("value")
print("raw:", repr(val)[:400])
try:
    d = json.loads(val)
    print()
    print(json.dumps(d, ensure_ascii=False, indent=2))
except Exception as e:
    print("parse fail:", e)
