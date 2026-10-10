# -*- coding: utf-8 -*-
"""一体化实测：触发子代理并抓取卡片的真实内容与状态。

之前分多次跑，每次都要重启后端+GUI 壳（GUI 壳退出会连带 kill 后端）。
本脚本一次跑完：等就绪 → 切平台 → 触发 → 轮询读 DOM → 截图。
"""
import io
import json
import os
import sys
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
    val = json.loads(_op.open(req, timeout=timeout).read()).get("value")
    if isinstance(val, str) and val.strip():
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


READ_CARD_JS = r"""
(() => {
  const b = document.querySelector('.subagent-block');
  if (!b) return JSON.stringify({exists: false});
  const r = b.getBoundingClientRect();
  const head = b.querySelector('.sa-head');
  const lines = b.querySelectorAll('.sa-line, .sa-item, .sa-entry, .sa-msg');
  const txt = (e) => (e && e.textContent ? e.textContent.replace(/\s+/g, ' ').trim() : '');
  return JSON.stringify({
    exists: true,
    h: Math.round(r.height), w: Math.round(r.width),
    visible: r.height > 0 && r.width > 0,
    head: txt(head).slice(0, 90),
    status: txt(b.querySelector('.sa-status')),
    lineCount: lines.length,
    lines: Array.from(lines).map(txt).map(s => s.slice(0, 95)).slice(0, 8),
    collapsed: b.classList.contains('collapsed'),
  });
})()
"""

# ── 1. 等就绪 ──
out.append("== 1. 等后端 + GUI 桥就绪 ==")
import psutil
ok8 = ok9 = False
for i in range(150):
    if not ok8:
        try:
            if _op.open(API + "/health", timeout=3).status == 200:
                ok8 = True
        except Exception:
            pass
    if not ok9:
        try:
            if [x for x in psutil.net_connections(kind="tcp")
                    if x.laddr and x.laddr[1] == 9333 and x.status == "LISTEN"]:
                ok9 = True
        except Exception:
            pass
    if ok8 and ok9:
        break
    time.sleep(2)
out.append("  后端 8888: %s   GUI 桥 9333: %s" % ("OK" if ok8 else "DOWN",
                                          "UP" if ok9 else "DOWN"))
if not (ok8 and ok9):
    out.append("[FATAL] 服务未就绪")
    io.open(os.path.join(ROOT, "_sa2_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, not ready")
    sys.exit(0)

# ── 2. 等 commander ready + 切平台 ──
out.append("")
out.append("== 2. 等 commander ready 并切平台 ==")
for i in range(150):
    try:
        h = json.loads(_op.open(API + "/health", timeout=5).read())
        if h.get("commander") == "ready":
            break
    except Exception:
        pass
    time.sleep(1)
out.append("  commander: %s" % h.get("commander"))
chk("commander ready", h.get("commander") == "ready", h.get("commander"))

req = urllib.request.Request(API + "/platform",
                             data=json.dumps({"platform": "deepseek"}).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
_op.open(req, timeout=30).read()
out.append("  平台: deepseek")

# ── 3. 触发子代理（字段名是 command，不是 text！）──
out.append("")
out.append("== 3. 触发子代理 ==")
req = urllib.request.Request(API + "/command",
                             data=json.dumps({"command": "请用 browser_research 子代理研究 "
                                                          "https://example.com 的页面标题和主要内容，"
                                                          "把研究报告告诉我。"},
                                             ensure_ascii=False).encode("utf-8"),
                             headers={"Content-Type": "application/json"}, method="POST")
try:
    r = _op.open(req, timeout=60).read().decode("utf-8", "replace")
    out.append("  /command: %s" % r[:160])
    chk("命令已受理", "accepted" in r, r[:90])
except Exception as e:
    chk("命令已受理", False, repr(e)[:110])

# ── 4. 轮询读卡片 ──
out.append("")
out.append("== 4. 轮询子代理卡 DOM（最多 180s）==")
card = None
t0 = time.time()
last = ""
while time.time() - t0 < 180:
    time.sleep(3)
    try:
        card = ev(READ_CARD_JS, 30)
    except Exception as e:
        card = {"exists": False, "err": repr(e)[:80]}
    if isinstance(card, dict) and card.get("exists"):
        cur = "lines=%s status=%s h=%s" % (card.get("lineCount"),
                                           card.get("status"), card.get("h"))
        if cur != last:
            out.append("  [%3ds] %s" % (int(time.time() - t0), cur))
            last = cur
        if card.get("lineCount", 0) >= 2:
            break

out.append("")
out.append("  卡片最终状态: %s" % json.dumps(card, ensure_ascii=False)[:600])

# ── 5. 断言 ──
out.append("")
out.append("== 5. 断言 ==")
c = card if isinstance(card, dict) else {}
chk("DOM 里有 .subagent-block 卡片", c.get("exists") is True, c.get("err", "不存在"))
chk("卡片可见（高度>0、宽度>0）", c.get("visible") is True,
    "h=%s w=%s" % (c.get("h"), c.get("w")))
chk("卡片有内容行（工具调用/思考）", c.get("lineCount", 0) > 0, "lineCount=%s" % c.get("lineCount"))
chk("卡片有标题文本", bool(c.get("head")), (c.get("head") or "")[:70])

out.append("")
out.append("  卡片标题: %s" % (c.get("head") or "(无)"))
out.append("  卡片状态: %s" % (c.get("status") or "(无)"))
out.append("  内容行:")
for ln in (c.get("lines") or []):
    out.append("     · %s" % ln)

# ── 6. 截图 ──
out.append("")
out.append("== 6. 截图 ==")
try:
    shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_subagent2.png")
    req = urllib.request.Request(BRIDGE, data=json.dumps({"kind": "shot", "path": shot}).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    _op.open(req, timeout=45).read()
    chk("截图已保存", os.path.isfile(shot), shot)
except Exception as e:
    chk("截图已保存", False, repr(e))

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
io.open(os.path.join(ROOT, "_sa2_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
