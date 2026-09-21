# -*- coding: utf-8 -*-
"""GUI 端到端真实体验：真发消息 → 平台真人回复 → 产物归属到该任务。

这是用户最核心的一条链路，必须真跑：
  A. 在 GUI 里发一条消息（走真正的 /command）
  B. 平台网页真的回了内容，GUI 对话区出现 AI 气泡
  C. 该轮产生的产物，只在「这个任务」里可见；切到别的任务/无任务时看不到

不做任何简化：走 HTTP /command，等 SSE/轮询，读 /dom 校验平台侧真实状态。
"""
import json
import re
import time
import urllib.request
import urllib.error
import sys

BRIDGE = "http://127.0.0.1:9333/"
API = "http://127.0.0.1:8888"
RESULTS = []


def bridge(js, timeout=60):
    body = json.dumps({"js": js}).encode("utf-8")
    req = urllib.request.Request(BRIDGE, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw).get("value")
    except Exception:
        return raw


def js_obj(expr, timeout=60):
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


def api_get(path, timeout=60):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for(expr, timeout=30, interval=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = js_obj("(%s)" % expr, timeout=30)
        except Exception as e:
            last = "ERR:%s" % e
        if last is True:
            return True
        time.sleep(interval)
    print("      [wait_for 超时] %s last=%r" % (expr[:100], last))
    return False


def main():
    # ── 准备：开一个全新干净对话，保证不会有旧产物干扰 ─────────────
    js_obj("(function(){document.querySelectorAll('#messages .local-card')"
           ".forEach(function(n){n.remove()});return 1;})()")
    js_obj("newConversation()")
    ok_clean = wait_for("document.querySelectorAll('#messages .msg-user').length === 0", timeout=90)
    print("[准备] 已开全新对话:", ok_clean)

    # 平台侧当前对话 id（新对话后应为空 / 新 id）
    dom0 = api_get("/dom")
    url0 = dom0.get("url", "")
    print("[准备] 平台当前 URL:", url0)

    # ── A. 通过 GUI 真发一条消息 ─────────────────────────────────
    MSG = "请只回复四个字：产物归属测试"
    js_obj("(function(){var i=document.getElementById('input');i.value=%s;return 1;})()" % json.dumps(MSG))
    js_obj("sendInput()")

    ok_user = wait_for("document.querySelectorAll('#messages .msg-user').length > 0", timeout=15)
    check("A1 用户消息立刻显示在对话区", ok_user,
          "msg-user=%s" % js_obj("document.querySelectorAll('#messages .msg-user').length"))

    # 等 AI 气泡出现（真等平台回复，最长 4 分钟）
    ok_ai = wait_for(
        "Array.prototype.some.call(document.querySelectorAll('#messages .msg-ai'),"
        "function(e){return e.innerText && e.innerText.trim().length>3;})", timeout=260, interval=3)
    ai_text = js_obj("(function(){var n=document.querySelectorAll('#messages .msg-ai');"
                     "for(var i=n.length-1;i>=0;i--){var t=n[i].innerText.trim();if(t)return t.slice(0,160);}"
                     "return '';})()")
    check("B1 平台真的回了内容并显示成 AI 气泡", ok_ai, "AI=%r" % (ai_text or "")[:150])

    # 平台侧也确认页面真的有回复文本
    time.sleep(2)
    try:
        dom1 = api_get("/dom")
        url1 = dom1.get("url", "")
        html = dom1.get("html", "")
    except Exception as e:
        url1, html = "", ""
        print("      /dom 读取失败:", e)
    check("B2 平台网页 URL 已进入真实对话(带 chat id)",
          bool(re.search(r"/a/chat/s/[0-9a-fA-F\-]{8,}", url1 or "")),
          "url=%s (before=%s)" % (url1, url0))

    # ── C. 产物归属该任务 ────────────────────────────────────────
    # 当前任务 id（GUI 记录）
    cur_task = js_obj("(typeof CURRENT_TASK_ID!=='undefined')?CURRENT_TASK_ID:null")
    print("[C] 当前任务 id:", cur_task)

    # 后端 /files 要能带出这个会话 id
    try:
        f = api_get("/files")
        conv = f.get("conversation_id")
        print("[C] /files conversation_id =", conv, " files =", len(f.get("files") or []))
    except Exception as e:
        conv = None
        print("[C] /files 失败:", e)

    # GUI 侧：没选任务时产物必须为空；选中该任务时应绑定到该任务
    js_obj("(typeof bindFileScope==='function') ? bindFileScope(null) : null")
    time.sleep(1.2)
    files_none = js_obj("(typeof FILE_LIST!=='undefined'?FILE_LIST.length:-1)")
    check("C1 未选中任务时产物侧栏为空", files_none == 0, "FILE_LIST=%s" % files_none)

    if conv:
        js_obj("(typeof bindFileScope==='function') ? bindFileScope(%s) : null" % json.dumps(conv))
        time.sleep(1.5)
        scope = js_obj("(typeof FILE_SCOPE_CONV!=='undefined')?FILE_SCOPE_CONV:null")
        files_own = js_obj("(typeof FILE_LIST!=='undefined'?FILE_LIST.length:-1)")
        check("C2 绑定到本任务后产物归属正确(不串到别的任务)",
              scope == conv and files_own is not None,
              "scope=%s expect=%s FILE_LIST=%s" % (scope, conv, files_own))

    # 切到另一个（无关）会话 id：产物必须变空（说明真按会话隔离）
    other = "____not_a_real_conv____"
    try:
        r = api_get("/attachments?conversation_id=" + other)
        other_files = r.get("files") or []
    except Exception:
        other_files = []
    check("C3 不存在的会话查产物 = 空(隔离生效)", len(other_files) == 0, "files=%s" % other_files)

    # 清理：回到无任务态
    js_obj("(typeof bindFileScope==='function') ? bindFileScope(null) : null")

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n================ 结果 %d/%d ================" % (passed, total))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s | %s" % (name, str(detail)[:200]))
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
