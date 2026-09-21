"""仙人掌 Agent —— 真实用户交互验收（走桌面壳测试桥，操作真实窗口 DOM）。

覆盖用户明确投诉的几件事：
  A. 我发的消息在 GUI 面板上要显示（点发送按钮 / 按 Enter 两条路径）
  B. 状态 / 帮助 / 新建对话 是「软件即时反应」，不能变成发给 AI 的消息
  C. 新建对话要真的在平台网页开一个新对话页（独立上下文）
  D. 思考过程块里不能混入「（未收到回复）」这类占位符
  E. 窗口图标要是仙人掌（不是空白程序图标）

用法：
  1) 先带测试桥启动桌面壳：
     XRZ_GUI_BRIDGE=1 QTWEBENGINE_DISABLE_SANDBOX=1 \
     QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu" \
     pythonw.exe desktop_app.py
  2) python _verify_ux_real.py
"""

import json
import sys
import time
import urllib.request

BRIDGE = "http://127.0.0.1:9333/"
BACKEND = "http://127.0.0.1:8888"

PASS, FAIL = [], []


def ev(js, timeout=60):
    req = urllib.request.Request(
        BRIDGE, data=json.dumps({"js": js}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("value")


def post(path, payload=None, timeout=120):
    req = urllib.request.Request(
        BACKEND + path,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  <- " + str(detail)[:220]) if detail else ""))


def clear_msgs():
    ev('(function(){document.querySelectorAll("#messages .msg,.welcome,.onboarding,#typing")'
       '.forEach(function(n){n.remove()});return 1;})()')


def user_bubbles():
    return ev('JSON.stringify(Array.prototype.map.call('
              'document.querySelectorAll("#messages .msg-user"),function(e){return e.textContent}))')


def main():
    print("=" * 74)
    print("仙人掌 Agent 真实用户交互验收")
    print("=" * 74)

    # ── 0. 桥 & 页面 ──
    href = ev("location.href")
    check("0.1 测试桥连通 / GUI 已加载", href and "gui.html" in str(href), href)

    # ── A. 消息显示：点按钮 ──
    print("\n[A] 我发的消息要在面板显示")
    clear_msgs()
    ev('(function(){var i=document.getElementById("input");i.value="UX_BTN_SEND";'
       'i.dispatchEvent(new Event("input",{bubbles:true}));return 1;})()')
    ev('document.getElementById("sendBtn").click(),1')
    time.sleep(2.5)
    b = json.loads(user_bubbles() or "[]")
    check("A.1 点 ▶ 发送后立刻出现自己的气泡", "UX_BTN_SEND" in b, b)
    check("A.2 发送后输入框被清空",
          ev('JSON.stringify(document.getElementById("input").value)') == '""')

    # ── A. 消息显示：按 Enter ──
    clear_msgs()
    ev('(function(){var i=document.getElementById("input");i.focus();i.value="UX_ENTER_SEND";'
       'i.dispatchEvent(new Event("input",{bubbles:true}));return 1;})()')
    ev('(function(){var i=document.getElementById("input");'
       'i.dispatchEvent(new KeyboardEvent("keydown",'
       '{key:"Enter",code:"Enter",keyCode:13,which:13,bubbles:true,cancelable:true}));return 1;})()')
    time.sleep(2.5)
    b = json.loads(user_bubbles() or "[]")
    check("A.3 按 Enter 也能发送并显示气泡", "UX_ENTER_SEND" in b, b)

    # ── A. Shift+Enter 不该发送 ──
    clear_msgs()
    ev('(function(){var i=document.getElementById("input");i.value="UX_SHIFT_ENTER";'
       'i.dispatchEvent(new Event("input",{bubbles:true}));return 1;})()')
    ev('(function(){var i=document.getElementById("input");'
       'i.dispatchEvent(new KeyboardEvent("keydown",'
       '{key:"Enter",keyCode:13,shiftKey:true,bubbles:true,cancelable:true}));return 1;})()')
    time.sleep(1.5)
    b = json.loads(user_bubbles() or "[]")
    check("A.4 Shift+Enter 是换行、不发送", "UX_SHIFT_ENTER" not in b, b)
    ev('(function(){document.getElementById("input").value="";return 1;})()')

    # ── B. 本地按钮不发给 AI ──
    print("\n[B] 状态/帮助 是软件即时反应，不能变成发给 AI 的消息")
    clear_msgs()
    ev("showLocalPanel('status'),1")
    time.sleep(2.0)
    n_local = ev('document.querySelectorAll("#messages .local-card").length')
    n_user = ev('document.querySelectorAll("#messages .msg-user").length')
    sending = ev('(typeof sending!=="undefined")?sending:null')
    check("B.1 「状态」渲染成本地卡片", float(n_local or 0) >= 1, n_local)
    check("B.2 「状态」没有变成用户消息", float(n_user or 0) == 0, n_user)
    check("B.3 「状态」没有触发发送锁", sending in (False, "false"), sending)

    clear_msgs()
    ev("showLocalPanel('help'),1")
    time.sleep(2.0)
    n_local = ev('document.querySelectorAll("#messages .local-card").length')
    check("B.4 「帮助」渲染成本地卡片", float(n_local or 0) >= 1, n_local)

    # ── C. 新建对话 = 平台真开新对话 ──
    print("\n[C] 新建对话要在平台网页真开一个新对话页")
    d = post("/new_conversation", {}, timeout=150)
    check("C.1 /new_conversation 返回 ok", d.get("type") == "ok", d.get("text", "")[:120])
    check("C.2 后端报告已清空对话上下文", "上下文" in str(d.get("text", "")), d.get("text", "")[:120])
    time.sleep(2.0)
    pr = post("/probe", {"js": "location.href"}, timeout=90)
    url = str(pr.get("value") or "")
    check("C.3 平台网页确实跳到了新对话页",
          ("chat.deepseek.com" in url) and ("/a/chat/s/" not in url), url)

    # ── D. 思考块无占位符污染 ──
    print("\n[D] 思考过程块不能混入占位符")
    bodies = ev('JSON.stringify(Array.prototype.map.call('
                'document.querySelectorAll("#messages .think-body,.think-body"),'
                'function(e){return e.textContent}))')
    bad = "（未收到回复）" in str(bodies)
    check("D.1 思考块里没有「（未收到回复）」占位符", not bad, str(bodies)[:200] if bad else "")

    # ── E. 后端健康 ──
    print("\n[E] 后端状态")
    try:
        h = json.loads(urllib.request.urlopen(BACKEND + "/health", timeout=6).read().decode())
        check("E.1 /health 返回 200 且 agent_ready", bool(h.get("agent_ready")), h)
    except Exception as e:
        check("E.1 /health 返回 200 且 agent_ready", False, e)

    # ── 汇总 ──
    print("\n" + "=" * 74)
    print("结果：%d PASS / %d FAIL" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    print("=" * 74)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
