# -*- coding: utf-8 -*-
"""实测：子代理卡在 GUI 里能不能显示（一直遗留未验证的问题）。

三层验证，缺一不可：
  A. 后端层：SSE 是否真的发出带 subagent_task_id 的事件（卡片的唯一数据源）
  B. 前端层：DOM 里是否真的长出子代理卡节点
  C. 端到端：两者都有才算通过

不用多行 async IIFE（GUI 桥执行不了，会返回空串），一律同步单行分步调用。
"""
import io
import json
import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
API = "http://127.0.0.1:8888"
BRIDGE = "http://127.0.0.1:9333/"

out = []
npass = nfail = 0


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-44s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


def ev(js, timeout=45):
    req = urllib.request.Request(BRIDGE, data=json.dumps({"kind": "eval", "js": js}).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(_op.open(req, timeout=timeout).read()).get("value")
    if isinstance(r, str) and r.strip():
        try:
            return json.loads(r)
        except Exception:
            return r
    return r


# ── 0. 前置检查 ──
try:
    h = json.loads(_op.open(API + "/health", timeout=8).read())
    out.append("[前置] health: %s" % json.dumps(h, ensure_ascii=False))
    chk("commander 已 ready（否则发不出消息）", h.get("commander") == "ready",
        h.get("commander"))
except Exception as e:
    out.append("[FATAL] 后端不可用: %r" % e)
    io.open(os.path.join(ROOT, "_sa_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, backend down")
    sys.exit(0)

# ── 1. 起 SSE 监听 ──
events = []
stop = threading.Event()
ready = threading.Event()


def listen():
    try:
        resp = _op.open(urllib.request.Request(API + "/events"), timeout=900)
        ready.set()
        buf = b""
        while not stop.is_set():
            try:
                chunk = resp.read(1)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b"\n\n"):
                raw = buf.decode("utf-8", "replace")
                buf = b""
                for line in raw.split("\n"):
                    if not line.startswith("data:"):
                        continue
                    pl = line[5:].strip()
                    if not pl or pl == "[DONE]":
                        continue
                    try:
                        events.append(json.loads(pl))
                    except Exception:
                        pass
    except Exception as e:
        out.append("[SSE] 异常: %r" % e)


t = threading.Thread(target=listen, daemon=True)
t.start()
ready.wait(15)
out.append("[SSE] 已连接: %s" % ready.is_set())
time.sleep(1.0)

# ── 2. 确认前端有子代理卡渲染函数 ──
out.append("")
out.append("== A. 前端代码完整性 ==")
fn = ev("JSON.stringify({upsert: typeof upsertSubagentCard, "
        "handle: typeof handleGuiEvent, cards: typeof SUBAGENT_CARDS})")
out.append("  %s" % (fn,))
if isinstance(fn, dict):
    chk("upsertSubagentCard 存在", fn.get("upsert") == "function", fn.get("upsert"))
    chk("SUBAGENT_CARDS 容器存在", fn.get("cards") == "object", fn.get("cards"))

# ── 3. 触发一次子代理 ──
out.append("")
out.append("== B. 触发子代理 ==")
# 【重要】/command 的字段名是 **command**，不是 text。
# 我先前用 {"text": ...} 一直拿到 400「空命令」，误判成"commander 没就绪"，
# 白查了半天。正确用法见 gui.html: body: JSON.stringify({ command: text, attachments: [...] })
req = urllib.request.Request(API + "/command",
                             data=json.dumps({"command": "请用 browser_visit 子代理打开 "
                                                          "https://example.com 看一下页面标题，"
                                                          "然后告诉我结果。"},
                                             ensure_ascii=False).encode("utf-8"),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    r = _op.open(req, timeout=60).read().decode("utf-8", "replace")
    out.append("  /command 返回: %s" % r[:200])
    chk("命令已受理（非 400 空命令）", '"error"' not in r and "空命令" not in r, r[:100])
except Exception as e:
    body = ""
    try:
        body = e.read().decode("utf-8", "replace")[:200]
    except Exception:
        pass
    out.append("  /command 失败: %r  body=%s" % (e, body))
    chk("命令已受理（非 400 空命令）", False, body or repr(e)[:100])

# ── 4. 等子代理事件 ──
out.append("")
out.append("== C. 等 SSE 子代理事件（最多 200s）==")
got = []
t0 = time.time()
while time.time() - t0 < 200:
    time.sleep(2)
    got = []
    for e in events:
        d = e.get("data") if isinstance(e.get("data"), dict) else e
        if isinstance(d, dict) and d.get("subagent_task_id"):
            got.append(d)
    if got:
        break
    el = int(time.time() - t0)
    if el % 20 == 0:
        out.append("  [%ds] 累计事件 %d 条" % (el, len(events)))

out.append("  抓到带 subagent_task_id 的事件: %d 条" % len(got))
chk("SSE 有 subagent_task_id 事件（卡片的数据源）", len(got) > 0,
    "共 %d 条 SSE 事件但无一含 subagent_task_id" % len(events))

if got:
    tids = set()
    etypes = {}
    for dg in got:
        if dg.get("subagent_task_id"):
            tids.add(dg["subagent_task_id"])
        et = dg.get("type") or "?"
        etypes[et] = etypes.get(et, 0) + 1
    out.append("     subagent_task_id: %s" % sorted(tids))
    out.append("     事件类型分布: %s" % etypes)
    chk("子代理事件带 task_id", bool(tids), tids)

# ── 5. 前端 DOM 是否长出卡片 ──
out.append("")
out.append("== D. 前端 DOM 卡片节点 ==")
time.sleep(2)
dom = ev(r"""(() => {
  const sel = ['.subagent-block','.subagent-card','[class*="subagent"]'];
  const found = {};
  let total = 0;
  sel.forEach(s => { const n = document.querySelectorAll(s).length; if (n) { found[s] = n; total += n; } });
  const cards = [];
  document.querySelectorAll('[class*="subagent"]').forEach(c => {
    const r = c.getBoundingClientRect();
    cards.push({cls: c.className, h: Math.round(r.height), w: Math.round(r.width)});
  });
  return JSON.stringify({total, found, cards: cards.slice(0,6)});
})()""")
out.append("  %s" % (dom,))
dd = dom if isinstance(dom, dict) else {}
chk("DOM 里有子代理卡节点", dd.get("total", 0) > 0, dom)

# ── 6. 截图 ──
out.append("")
out.append("== E. 截图 ==")
try:
    shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_subagent.png")
    req = urllib.request.Request(BRIDGE, data=json.dumps({"kind": "shot", "path": shot}).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    _op.open(req, timeout=45).read()
    chk("截图已保存", os.path.isfile(shot), shot)
except Exception as e:
    chk("截图已保存", False, repr(e))

stop.set()
out.append("")
out.append("== 汇总 ==")
out.append("  SSE 总事件数: %d" % len(events))
out.append("  subagent 事件: %d" % len(got))
out.append("  DOM 卡片节点: %d" % dd.get("total", 0))
out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))

io.open(os.path.join(ROOT, "_sa_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
