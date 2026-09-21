# -*- coding: utf-8 -*-
"""复现并验证「用户消息气泡消失」的真实场景。

2026-09-21 实测到的 GUI 严重 bug：
  用户点「📚 历史记录」看了旧任务 → 回到实时视图 → 点「➕ 新建对话」→ 发消息，
  刚发出去的用户气泡会【凭空消失】，只剩系统日志和 AI 回复。

根因（已修）：openHistoryTask() 会把实时视图的 HTML 快照存进 LIVE_DOM_CACHE；
closeHistoryView() 无条件把这份【旧快照】写回 msgDiv.innerHTML。
而 newConversation() 里是「先 closeHistoryView() 再 resetChatView()」以外的
时序，导致旧快照盖掉了刚清空/刚写入的用户消息；更糟的是 HIST_VIEW 已经是 null
时 closeHistoryView 直接 return，LIVE_DOM_CACHE 永远不清，残留到后面某次调用再爆发。

本脚本严格按用户操作顺序走一遍，断言用户气泡必须一直在。
"""
import json
import time
import urllib.request
import sys

BRIDGE = "http://127.0.0.1:9333/"
API = "http://127.0.0.1:8888"
RESULTS = []


def bridge(js, timeout=45):
    body = json.dumps({"js": js}).encode("utf-8")
    req = urllib.request.Request(BRIDGE, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


def obj(expr, timeout=45):
    v = bridge("JSON.stringify(%s)" % expr, timeout)
    if v in (None, "", "undefined", "null"):
        return None
    try:
        return json.loads(v)
    except Exception:
        return v


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + str(detail)[:240] if detail else ""))


def wait_for(expr, timeout=25, interval=0.6):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = obj("(%s)" % expr, timeout=25)
        except Exception as e:
            last = "ERR:%s" % e
        if last is True:
            return True
        time.sleep(interval)
    print("      [wait_for 超时] %s last=%r" % (expr[:90], last))
    return False


def n_user():
    return obj("document.querySelectorAll('#messages .msg-user').length")


def user_texts():
    return obj("(function(){var n=document.querySelectorAll('#messages .msg-user');"
               "var o=[];for(var i=0;i<n.length;i++)o.push(n[i].innerText.trim());return o;})()") or []


def main():
    check("测试桥连通", bridge('"ok"') == "ok")

    # ── 场景 1：点历史记录 → 打开一条 → 关闭 → 新建对话 → 发消息 ──
    print("\n[场景1] 看历史 → 回实时 → 新建对话 → 发消息（气泡必须还在）")
    obj("toggleHistory(false)")
    obj("toggleHistory(true)")
    wait_for("document.querySelectorAll('#historyList .history-item').length > 0", timeout=20)
    obj("(function(){var it=document.querySelector('#historyList .history-item');if(it)it.click();return 1;})()")
    wait_for("!!document.querySelector('#messages .hist-banner')", timeout=15)
    in_hist = obj("!!document.querySelector('#messages .hist-banner')")
    check("1.1 确实进入了历史回看视图", in_hist is True, "hist-banner=%s" % in_hist)
    cache_after_open = obj("(typeof LIVE_DOM_CACHE!=='undefined' && LIVE_DOM_CACHE!==null) ? LIVE_DOM_CACHE.length : 0")
    print("      进入历史后 LIVE_DOM_CACHE 长度 =", cache_after_open)

    obj("toggleHistory(false)")
    obj("newConversation()")
    ok_reset = wait_for("document.querySelectorAll('#messages .msg-user').length === 0", timeout=90)
    cache_after_new = obj("(typeof LIVE_DOM_CACHE!=='undefined' && LIVE_DOM_CACHE!==null) ? LIVE_DOM_CACHE.length : 0")
    check("1.2 新建对话后 LIVE_DOM_CACHE 已清空(不再残留旧快照)", cache_after_new == 0 or cache_after_new is None,
          "cache len=%s (进入历史时=%s)" % (cache_after_new, cache_after_open))

    # 发消息
    obj("sendInput('__BUBBLE_AFTER_HISTORY__')")
    ok_bubble = wait_for("document.querySelectorAll('#messages .msg-user').length > 0", timeout=15)
    texts = user_texts()
    check("1.3 【核心】看历史后发消息，用户气泡仍然出现", ok_bubble and len(texts) >= 1,
          "气泡数=%s 内容=%s" % (len(texts), texts[:2]))

    # 等 AI 回复后，气泡不能被 SSE 事件顶掉
    wait_for("document.querySelectorAll('#messages .msg-ai').length > 0", timeout=180, interval=3)
    texts2 = user_texts()
    check("1.4 AI 回复到达后用户气泡依然在(没被 SSE 清掉)",
          any("__BUBBLE_AFTER_HISTORY__" in t for t in texts2),
          "气泡内容=%s" % texts2[:3])

    # ── 场景 2：直接新建对话 → 发消息（对照） ──
    print("\n[场景2] 直接新建对话 → 发消息（对照）")
    obj("newConversation()")
    wait_for("document.querySelectorAll('#messages .msg-user').length === 0", timeout=90)
    obj("sendInput('__BUBBLE_DIRECT__')")
    ok2 = wait_for("document.querySelectorAll('#messages .msg-user').length > 0", timeout=15)
    t2 = user_texts()
    check("2.1 直接新建后发消息，气泡出现", ok2 and any("__BUBBLE_DIRECT__" in t for t in t2),
          "气泡=%s" % t2[:2])

    # ── 场景 3：反复开关历史抽屉不应影响气泡 ──
    print("\n[场景3] 反复开关历史抽屉不影响当前气泡")
    obj("toggleHistory(true)"); time.sleep(0.8)
    obj("toggleHistory(false)"); time.sleep(0.8)
    obj("toggleHistory(true)"); time.sleep(0.8)
    obj("toggleHistory(false)"); time.sleep(0.8)
    t3 = user_texts()
    check("3.1 开关历史抽屉后当前气泡仍在",
          any("__BUBBLE_DIRECT__" in t for t in t3), "气泡=%s" % t3[:3])

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n================ 结果 %d/%d ================" % (passed, total))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s | %s" % (name, str(detail)[:200]))
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
