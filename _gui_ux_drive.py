# -*- coding: utf-8 -*-
"""真实 GUI 用户体验驱动脚本（走桌面壳测试桥 9333）。

只做「用户真会做的动作」：
  1. 打开历史抽屉 → 看得到历史任务
  2. 点一条历史任务 → 进入回看视图
  3. 发一条消息 → 用户气泡立刻出现（不被吃掉）
  4. 点 📊 状态 / ❓ 帮助 → 本地即时卡片，绝不进发送流
  5. 点 ➕ 新建对话 → 消息区清空 + 平台网页真的开新对话
  6. 产物侧栏作用域：无任务时必须为空

断言全部基于 DOM 实测，不看日志。
"""
import json
import re
import time
import urllib.request
import sys

BRIDGE = "http://127.0.0.1:9333/"
API = "http://127.0.0.1:8888"

RESULTS = []


def bridge(js, timeout=40):
    body = json.dumps({"js": js}).encode("utf-8")
    req = urllib.request.Request(BRIDGE, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


def js_obj(expr, timeout=40):
    """在页面里求值，JSON.stringify 回传后解析成 Python 对象。"""
    v = bridge("JSON.stringify(%s)" % expr, timeout)
    if v in (None, "", "undefined", "null"):
        return None
    try:
        return json.loads(v)
    except Exception:
        return v


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + str(detail)[:220] if detail else ""))


def wait_for(expr, timeout=25, interval=0.6):
    """轮询等待页面表达式为真。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = js_obj("(%s)" % expr, timeout=20)
        except Exception as e:
            last = "ERR:%s" % e
        if last is True:
            return True
        time.sleep(interval)
    print("      [wait_for 超时] %s  last=%r" % (expr[:90], last))
    return False


def main():
    # ── 0. 基础环境 ───────────────────────────────────────────────
    v = bridge('"ok"')
    check("测试桥连通", v == "ok", v)

    info = js_obj("({href: location.href, api: (typeof API!=='undefined'?API:'?')})")
    check("GUI 页面已载入", bool(info) and "gui.html" in (info or {}).get("href", ""), info)

    # ── 1. 历史记录抽屉 ───────────────────────────────────────────
    js_obj("toggleHistory(false)")
    js_obj("toggleHistory(true)")
    ok = wait_for("document.querySelectorAll('#historyList .history-item').length > 0", timeout=20)
    n_hist = js_obj("document.querySelectorAll('#historyList .history-item').length")
    check("点『📚 历史记录』能看到历史任务", ok and (n_hist or 0) > 0, "历史条目=%s" % n_hist)

    # ── 2. 打开一条历史任务（回看视图）───────────────────────────
    first = js_obj("(function(){var it=document.querySelector('#historyList .history-item');"
                   "if(!it)return null;it.click();return true;})()")
    ok2 = wait_for("!!document.querySelector('#messages .hist-note, #messages .thinking-block')", timeout=20)
    hist_title = js_obj("(function(){var e=document.querySelector('#messages .hist-note');return e?e.innerText.slice(0,60):'';})()")
    check("点历史任务能进入回看视图", bool(first) and ok2, "first=%s note=%r" % (first, hist_title))

    js_obj("(typeof closeHistoryView==='function') && closeHistoryView()")
    js_obj("(typeof toggleHistory==='function') && toggleHistory(false)")
    time.sleep(1)

    # ── 3. 产物侧栏作用域：没选任务时必须为空 ─────────────────────
    js_obj("(typeof bindFileScope==='function') ? bindFileScope(null) : null")
    time.sleep(1.2)
    files = js_obj("(typeof FILE_LIST!=='undefined'?FILE_LIST.length:-1)")
    empty_txt = js_obj("(function(){var e=document.querySelector('#fileList');return e?e.innerText.trim():'';})()")
    check("无任务时产物侧栏为空", files == 0, "FILE_LIST=%s text=%r" % (files, (empty_txt or "")[:60]))

    # ── 4. 用户消息立刻显示（不被吃掉）────────────────────────────
    js_obj("resetChatView && resetChatView()")
    time.sleep(1)
    before = js_obj("document.querySelectorAll('#messages .msg-user').length")
    js_obj("(function(){var i=document.getElementById('input');i.value='__UX_DRIVE__';})()")
    js_obj("sendInput('__UX_DRIVE__')")
    ok4 = wait_for("document.querySelectorAll('#messages .msg-user').length > %d" % (before or 0), timeout=8)
    after = js_obj("document.querySelectorAll('#messages .msg-user').length")
    check("自己发的消息立刻出现在对话区", ok4 and (after or 0) > (before or 0),
          "before=%s after=%s" % (before, after))

    # ── 5. 📊 状态 / ❓ 帮助：本地即时、不进发送流 ────────────────
    # 【测试严谨性】上一张本地卡片要等 8 秒才自动淡出，直接查 .local-card 会
    # 拿到上一张的残留（之前就把 'help' 误判成显示了 status 的内容）。
    # 这里每次都先彻底移除旧卡片，再等新卡片出现，并校验内容关键字与 kind 对应。
    for kind, kw, want in (("status", "状态", "运行状态"), ("help", "帮助", "使用帮助")):
        js_obj("(function(){document.querySelectorAll('#messages .local-card')"
               ".forEach(function(n){n.remove()});return 1;})()")
        time.sleep(0.4)
        t0 = time.time()
        js_obj("showLocalPanel('%s')" % kind)
        ok5 = wait_for("document.querySelectorAll('#messages .local-card').length > 0", timeout=12)
        dt = time.time() - t0
        # 收集当前全部本地卡片文本，校验「只出现 1 张 + 内容对得上 kind」
        cards = js_obj("(function(){var n=document.querySelectorAll('#messages .local-card');"
                       "var o=[];for(var i=0;i<n.length;i++)o.push(n[i].innerText.slice(0,80));return o;})()") or []
        joined = " ".join(cards)
        sending = js_obj("(typeof sending!=='undefined')?sending:null")
        check("点『%s』本地即时出卡片" % kw, ok5 and dt < 8 and len(cards) == 1,
              "耗时=%.1fs 卡片数=%s text=%r" % (dt, len(cards), joined[:70]))
        check("『%s』卡片内容正确(kind 未串台)" % kw, want in joined,
              "期望含 %r，实际=%r" % (want, joined[:90]))
        check("『%s』未进入发送流(sending 未置位)" % kw, sending is False, "sending=%s" % sending)
    # 清掉本地卡片，避免后续断言被残留干扰
    js_obj("(function(){document.querySelectorAll('#messages .local-card')"
           ".forEach(function(n){n.remove()});return 1;})()")

    # ── 6. 新建对话：清空对话区 + 平台网页真开新对话 ──────────────
    # 【测试严谨性】resetChatView() 会插入「已开启全新对话」+ welcome 两块，
    # 它们是 .msg 而不是用户消息。旧断言用 `msg 数变少` 判定「清空」，即使残留
    # 2 块也会误判为通过。这里必须断言：用户气泡=0 且历史思考块=0。
    js_obj("(function(){document.querySelectorAll('#messages .local-card')"
           ".forEach(function(n){n.remove()});return 1;})()")
    # 先造一条用户气泡，确保「清空」是可观测的真清空
    js_obj("sendInput('__UX_NEWCONV__')")
    wait_for("document.querySelectorAll('#messages .msg-user').length > 0", timeout=8)
    user_before = js_obj("document.querySelectorAll('#messages .msg-user').length")
    think_before = js_obj("document.querySelectorAll('#messages .thinking-block').length")
    try:
        url_before = json.loads(urllib.request.urlopen(API + "/dom", timeout=40).read().decode("utf-8")).get("url")
    except Exception as e:
        url_before = "ERR:%s" % e
    t0 = time.time()
    js_obj("newConversation()")
    ok6 = wait_for("document.querySelectorAll('#messages .msg-user').length === 0 "
                   "&& document.querySelectorAll('#messages .thinking-block').length === 0",
                   timeout=90)
    dt6 = time.time() - t0
    user_after = js_obj("document.querySelectorAll('#messages .msg-user').length")
    think_after = js_obj("document.querySelectorAll('#messages .thinking-block').length")
    check("点『新建对话』对话区真的清空(用户气泡与思考块归零)",
          ok6 and user_after == 0 and think_after == 0,
          "user %s->%s think %s->%s 耗时=%.1fs" % (user_before, user_after, think_before, think_after, dt6))
    # 新建后必须只剩「系统提示 + 欢迎块」，不能残留旧对话气泡
    classes = js_obj("(function(){var l=document.querySelectorAll('#messages .msg');var o=[];"
                     "for(var i=0;i<l.length;i++)o.push(l[i].className);return o;})()") or []
    check("新建对话后只剩系统提示(无残留消息)",
          all("msg-system" in c for c in classes), classes)

    # 平台侧 URL 必须换一个新的（或回到首页），且新页面不能带着旧对话的消息
    ok7 = False
    url_after = url_before
    dom_msgs = None
    for _ in range(20):
        time.sleep(1.5)
        try:
            dom = json.loads(urllib.request.urlopen(API + "/dom", timeout=40).read().decode("utf-8"))
            url_after = dom.get("url")
        except Exception:
            continue
        if url_after and url_after != url_before:
            ok7 = True
            break
    check("平台网页真的开了新对话(URL 已离开旧会话)", ok7,
          "before=%s -> after=%s" % (url_before, url_after))
    # 强校验：新页面必须已经不在旧 chat id 上（旧 id 出现在 URL 里 = 没真开新对话）
    old_id = None
    m = re.search(r"/a/chat/s/([0-9a-fA-F\-]{8,})", str(url_before or ""))
    if m:
        old_id = m.group(1)
        check("新对话 URL 不含旧会话 id", old_id not in str(url_after or ""),
              "old_id=%s after=%s" % (old_id, url_after))
    # 平台页面消息数：新对话应为 0
    if isinstance(dom_msgs, int):
        check("平台新对话页消息数为 0", dom_msgs == 0, "count=%s" % dom_msgs)

    # ── 7. 产物侧栏在新建对话后依然为空（新对话没有产物）─────────
    time.sleep(1.5)
    files2 = js_obj("(typeof FILE_LIST!=='undefined'?FILE_LIST.length:-1)")
    check("新建对话后产物侧栏为空", files2 == 0, "FILE_LIST=%s" % files2)

    # ── 汇总 ─────────────────────────────────────────────────────
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n================ 结果 %d/%d ================" % (passed, total))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s | %s" % (name, str(detail)[:200]))
    return passed == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
