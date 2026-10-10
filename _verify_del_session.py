# -*- coding: utf-8 -*-
"""实测：删除【当前会话】应回退到初始页面（用户明确要求的行为）。

不模拟鼠标：通过 GUI 桥调页面自身的 deleteSession() —— 与用户点 ✕ 同一条路径。
【踩坑】桥执行不了多行 async IIFE（返回空串），必须拆成同步单行分步调用；
对象/数组返回会退化成空串，一律 JSON.stringify 成字符串。
"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BRIDGE = "http://127.0.0.1:9333/"

out = []
npass = nfail = 0


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


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-40s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


# 0) 桥就绪
ev("1")

# 1) 自动确认 + 造消息
out.append("== 准备 ==")
ev("(() => { window.__rc = window.confirm; window.confirm = () => true; return 'hooked'; })()")
ev("(() => { const m = document.getElementById('messages');"
   "if (m) m.insertAdjacentHTML('beforeend',"
   "'<div class=\"msg msg-user\">ZZTESTZZ 删除当前会话后应清空</div>');"
   "return 'seeded'; })()")
st0 = ev("JSON.stringify({cur: String(CURRENT_SESSION),"
         "has: (document.getElementById('messages')||{}).innerHTML.indexOf('ZZTESTZZ') >= 0})")
out.append("  初始: %s" % (st0,))

# 2) 触发删除当前会话（同步发起，不 await 内部 Promise）
out.append("")
out.append("== 执行 deleteSession(当前会话) ==")
cur = None
try:
    cur = (st0 or {}).get("cur") if isinstance(st0, dict) else None
except Exception:
    cur = None
if cur in (None, "null", "undefined"):
    out.append("  没有当前会话，先创建一个")
    ev("sessionCmd('create')")
    time.sleep(1.5)
    ev("loadSessions()")

ev("(() => { try { deleteSession(CURRENT_SESSION); return 'called'; }"
   "catch(e) { return 'ERR '+e.message; } })()", 60)
time.sleep(4.0)   # 等异步链（后端删除 + resetChatView）走完

# 3) 断言回退到初始页
out.append("")
out.append("== 断言 ==")
after = ev("JSON.stringify({cur: String(CURRENT_SESSION),"
           "task: String(CURRENT_TASK_ID),"
           "hasMsg: (document.getElementById('messages')||{}).innerHTML.indexOf('ZZTESTZZ') >= 0,"
           "welcome: (document.getElementById('messages')||{}).innerHTML.indexOf('welcome') >= 0,"
           "sending: (typeof sending !== 'undefined') ? sending : null,"
           "msgCount: (document.getElementById('messages')||{children:{}}).children.length})")
out.append("  删除后: %s" % (after,))
a = after if isinstance(after, dict) else {}
chk("CURRENT_SESSION 已清空", a.get("cur") in ("null", "None"), a.get("cur"))
chk("CURRENT_TASK_ID 已清空", a.get("task") in ("null", "None"), a.get("task"))
chk("旧消息已清空(回退初始页)", a.get("hasMsg") is False, "hasMsg=%s" % a.get("hasMsg"))
chk("显示新对话欢迎页", a.get("welcome") is True, "welcome=%s" % a.get("welcome"))
chk("发送锁已释放", a.get("sending") is False, "sending=%s" % a.get("sending"))

# 4) 会话列表：每个都有删除按钮（含当前）
out.append("")
out.append("== 会话列表 ==")
sl = ev("(() => { loadSessions(); const it = document.querySelectorAll('.session-item');"
        "return JSON.stringify({count: it.length,"
        "btns: document.querySelectorAll('.session-item .si-delete').length,"
        "cur: String(CURRENT_SESSION)}); })()")
time.sleep(1.0)
out.append("  %s" % (sl,))
d = sl if isinstance(sl, dict) else {}
chk("每个会话都有删除按钮", d.get("btns") == d.get("count"),
    "btns=%s count=%s" % (d.get("btns"), d.get("count")))

# 5) 截图
try:
    shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_del_session.png")
    req = urllib.request.Request(BRIDGE, data=json.dumps({"kind": "shot", "path": shot}).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    _op.open(req, timeout=40).read()
    chk("截图", os.path.isfile(shot), shot)
except Exception as e:
    chk("截图", False, repr(e))

# 恢复 confirm
ev("(() => { if (window.__rc) window.confirm = window.__rc; return 'restored'; })()")

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
io.open(os.path.join(ROOT, "_delsess_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
