# -*- coding: utf-8 -*-
"""像真实用户一样操作 GUI 面板，逐项记录每个控件的行为是否正常（v2 修正版）。

v1 的假阳性已修：
  - theme   : 按钮没有文字标签 → 改用 #themeToggle；断言 classList 里的 'light'
  - sidebar : 收起是加 .collapsed 类，宽度变化在动画期间不稳 → 断言 classList
  - status/help : local-card 8 秒后自动移除 → 缩短探测延迟，双次采样
  - new_conversation : /new_conversation 是同步长请求（要真的在平台网页开新对话页）
                        → 轮询等待，不再固定 4 秒
  - no_protocol_leak : 只看真正的内部协议标记 @@@@ / safe-delete，不看普通文本里的 "tool":
新增（用户最在意的一项）：
  - history_task_products : 打开历史记录 → 点一个确实产出过文件的任务 →
                            右侧「文件产物」必须列出该任务的产物（修复前恒为空）
  - product_preview       : 点产物 → 预览区必须出内容

用法: python _drive_gui_audit.py
产出: _gui_audit_result.json + 控制台表格 + 截图/PDF 留痕
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_op)

ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
API = "http://127.0.0.1:8888"
BRIDGE = "http://127.0.0.1:9333"
DUMP = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_dumps")
os.makedirs(DUMP, exist_ok=True)

import psutil

RESULTS = []


def log(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print("  [%s] %-26s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:280]),
          flush=True)


def bridge(js_src, kind="eval", path="", timeout=45):
    data = {"js": js_src, "kind": kind, "path": path}
    req = urllib.request.Request(
        BRIDGE + "/", data=json.dumps(data, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    return r.get("value")


def js(expr, timeout=45, tries=3):
    wrapped = ("JSON.stringify((function(){ try { return (" + expr +
               "); } catch(e) { return {__err:String(e)}; } })())")
    for _ in range(tries):
        v = bridge(wrapped, timeout=timeout)
        if v not in (None, "", "null"):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return v
            return v
        time.sleep(1.2)
    return None


def kill_stale():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info.get("cmdline") or [])
            if p.info.get("name") in ("pythonw.exe", "python.exe") and "desktop_app.py" in cl:
                p.kill()
                print("  killed stale gui pid=", p.info["pid"], flush=True)
        except Exception:
            pass


def port_open(port):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def msg_state():
    return js("(function(){var m=document.getElementById('messages');"
              "return {total:document.querySelectorAll('#messages > *').length,"
              "user:document.querySelectorAll('#messages .msg-user').length,"
              "err:document.querySelectorAll('#messages .msg-error').length,"
              "local:document.querySelectorAll('#messages .local-card').length,"
              "text:(m.innerText||'').slice(0,3000)};})()")


def local_cards():
    """所有本地面板卡（状态/帮助/会话）的正文，拼起来。

    不要用 #messages.innerText 的 600 字前缀去判断 —— 对话区里本来就有内容时，
    新加的卡片会落在截断窗口之外，看起来像「点了没反应」（实测踩过）。
    """
    return js("Array.from(document.querySelectorAll('#messages .local-card-body'))"
              ".map(function(e){return e.textContent;}).join(' ⟂ ')")


def wait_local_card(needle, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        got = local_cards()
        if needle in str(got):
            return True
        time.sleep(0.35)
    return False


def click_button(text):
    return js("(function(){var bs=Array.from(document.querySelectorAll('button'));"
              "var b=bs.find(function(x){return (x.innerText||'').indexOf(%s)>=0;});"
              "if(!b) return 'NOT_FOUND'; b.click(); return 'CLICKED';})()" % json.dumps(text))


def click_id(eid):
    return js("(function(){var b=document.getElementById(%s);if(!b)return 'NOT_FOUND';"
              "b.click();return 'CLICKED';})()" % json.dumps(eid))


def wait_for_text(needle, timeout=60, step=2.0):
    """等消息区出现某段文字，返回实际文本。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        st = msg_state() or {}
        last = st.get("text", "") or ""
        if needle and needle in last:
            return last
        time.sleep(step)
    return last


def products_of(task_ids):
    """从后端问出「哪些任务确实有产物」，返回有产物的任务 id 列表。"""
    out = []
    for tid in task_ids:
        try:
            r = urllib.request.urlopen(
                API + "/attachments?conversation_id=" + urllib.parse.quote(tid),
                timeout=20)
            d = json.loads(r.read().decode())
            if d.get("files"):
                out.append((tid, d["files"]))
        except Exception:
            pass
    return out


def main():
    print("== GUI 面板真机巡检 v2 ==", flush=True)

    # 0) 后端必须先活着（GUI 的发送资格依赖 /health.agent_ready）
    try:
        h = json.loads(urllib.request.urlopen(API + "/health", timeout=10).read().decode())
        print("  后端 /health:", h, flush=True)
    except Exception as e:
        log("backend_health", False, str(e))
        return 1

    kill_stale()
    time.sleep(2)
    env = dict(os.environ)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(v, None)
    env["XRZ_GUI_BRIDGE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
    logf = open(os.path.join(ROOT, "_gui_audit_app.log"), "w", encoding="utf-8")
    gui = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                           stdout=logf, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        attached = False
        while time.time() - t0 < 150:
            if port_open(9333):
                try:
                    if js("1+1") == 2:
                        attached = True
                        break
                except Exception:
                    pass
            time.sleep(2)
        if not attached:
            log("gui_bridge", False, "测试桥连不上")
            return 1
        print("  测试桥已连接", flush=True)

        t0 = time.time()
        while time.time() - t0 < 120:
            if js("!!(document.getElementById('input')&&document.getElementById('sendBtn'))"):
                break
            time.sleep(2)
        time.sleep(4)

        # ---------- 0) 面板结构 ----------
        btns = js("Array.from(document.querySelectorAll('button')).map(function(b){"
                  "return (b.id?('#'+b.id):'')+':'+((b.innerText||'').replace(/\\s+/g,' ').trim().slice(0,16));})")
        log("buttons_listed", bool(btns), "按钮 %s 个: %s" % (len(btns or []), str(btns or [])[:320]))

        # ---------- 1) 状态：本地即时响应，不进对话、不发 AI ----------
        before = msg_state() or {}
        r = click_button("状态")
        got_status = wait_local_card("运行状态")
        after = msg_state() or {}
        log("status_is_local",
            r == "CLICKED" and got_status and after.get("user", 0) <= before.get("user", 0),
            "click=%s 出现'运行状态'=%s user气泡 %s->%s"
            % (r, got_status, before.get("user"), after.get("user")))

        # ---------- 2) 帮助：同样本地 ----------
        r = click_button("帮助")
        got_help = wait_local_card("使用帮助")
        log("help_is_local", r == "CLICKED" and got_help,
            "click=%s 出现'使用帮助'=%s 卡片=%r"
            % (r, got_help, str(local_cards())[:200]))

        # ---------- 3) 内部报文泄漏 ----------
        allt = js("(document.getElementById('messages').innerText||'')")
        leak = [k for k in ("@@@@", "safe-delete", "SAFE_DELETE", "session_manager")
                if k in str(allt)]
        log("no_protocol_leak", not leak, "泄漏关键字=%s" % leak)

        # ---------- 4) 平台切换 ----------
        r = click_button("豆包")
        time.sleep(3)
        active = js("(function(){var a=document.querySelector('#platformList button.active');"
                    "return a?a.innerText.trim():null;})()")
        log("platform_switch", bool(active), "点击豆包后 active=%r" % active)

        # ---------- 4b) 「思考」控件真的渲染出来了 ----------
        # 旧实现是空壳函数：清空容器后什么都不画，界面上只剩一个悬空的
        # 「🧠 思考:」标签（截图实证）。这里要求：要么整组隐藏，要么有真控件。
        th = js("(function(){var g=document.getElementById('thinkingGroup');"
                "var b=document.getElementById('thinkingToggleBtn');"
                "var gc=g?getComputedStyle(g).display:'none';"
                "if(gc==='none') return {hidden:true};"
                "return {hidden:false,text:b?b.textContent:'',"
                "w:b?Math.round(b.getBoundingClientRect().width):0};})()")
        log("thinking_control_rendered",
            bool(th and (th.get("hidden") is True
                         or (th.get("text") and th.get("w", 0) > 20))),
            "思考控件=%s" % str(th)[:160])

        # ---------- 5) 主题切换（#themeToggle，断言 classList） ----------
        th0 = js("document.documentElement.classList.contains('light')")
        r = click_id("themeToggle")
        time.sleep(1.5)
        th1 = js("document.documentElement.classList.contains('light')")
        label = js("(document.getElementById('themeToggle')||{}).textContent")
        log("theme_toggle", bool(th0 != th1),
            "light %s -> %s (click=%s, 按钮=%r)" % (th0, th1, r, label))
        if th1 is True:                       # 复位成深色（当前 IDE 是 dark）
            click_id("themeToggle")
            time.sleep(0.8)

        # ---------- 6) 左侧栏收起/展开 ----------
        c0 = js("document.getElementById('leftSidebar').classList.contains('collapsed')")
        r = click_id("sidebarToggle")
        time.sleep(1.2)
        c1 = js("document.getElementById('leftSidebar').classList.contains('collapsed')")
        log("sidebar_toggle", bool(c0 != c1), "collapsed %s -> %s (click=%s)" % (c0, c1, r))
        if c1 is True:
            click_id("sidebarToggle")
            time.sleep(1.0)

        # ---------- 7) 右侧文件栏收起/展开 ----------
        d0 = js("document.getElementById('fileSidebar').classList.contains('collapsed')")
        r = click_id("fileSidebarToggle")
        time.sleep(1.2)
        d1 = js("document.getElementById('fileSidebar').classList.contains('collapsed')")
        log("filepreview_toggle", bool(d0 != d1), "collapsed %s -> %s (click=%s)" % (d0, d1, r))
        if d1 is True:
            click_id("fileSidebarToggle")
            time.sleep(1.0)

        # ---------- 7b) 底部工具条不被裁切 ----------
        # 右侧预览栏展开后中央区变窄，工具条若不换行，最右的控件会被裁掉半截
        # （截图实证过）。这里直接量溢出。
        clip = js("(function(){var bar=document.querySelector('.toolbar-bottom');"
                  "if(!bar) return null; var br=bar.getBoundingClientRect();"
                  "var bad=[]; Array.prototype.forEach.call("
                  "bar.querySelectorAll('.tool-group'),function(g){"
                  "var r=g.getBoundingClientRect();"
                  # display:none 的元素 rect 全 0，会被误判成"超出左边界" → 先跳过
                  "if(r.width===0&&r.height===0) return;"
                  "if(r.right>br.right+1||r.left<br.left-1) bad.push(g.id||g.className);});"
                  "return {barW:Math.round(br.width),scrollW:bar.scrollWidth,"
                  "overflow:bar.scrollWidth>bar.clientWidth+1,bad:bad};})()")
        log("toolbar_not_clipped", bool(clip and not clip.get("overflow")
                                       and not clip.get("bad")),
            "工具条=%s" % str(clip)[:200])

        # ---------- 8) 历史记录抽屉 ----------
        r = click_button("历史记录")
        time.sleep(3)
        hd = js("(function(){var e=document.getElementById('historyDrawer');"
                "var st=getComputedStyle(e);"
                "return {vis:st.visibility,op:st.opacity,"
                "items:document.querySelectorAll('#historyList .history-item').length,"
                "text:(document.getElementById('historyList').innerText||'').slice(0,200)};})()")
        log("history_drawer", bool(r == "CLICKED" and hd and hd.get("items", 0) > 0),
            "click=%s 抽屉=%s" % (r, str(hd)[:240]))

        # ---------- 9) 【核心】点历史任务 → 该任务产物必须出现在右侧 ----------
        # 先等后端把手上的任务跑完：任务收尾会刷新产物栏，跟本步骤抢界面
        # （GUI 侧已修成"不抢用户正在看的任务"，这里再等一等让结果确定）。
        for _ in range(30):
            busy = js("(function(){ return (TASKS||[]).some(function(t){"
                      "return t.status==='running';}); })()")
            if busy is False or busy == "false":
                break
            time.sleep(3)
        # 先从后端问出「确实有产物」的任务 id，再在抽屉里点中对应那条。
        try:
            tj = json.loads(open(os.path.join(
                ROOT, "xrz_data", ".xianrenzhang_agent", "tasks.json"),
                encoding="utf-8").read())
            all_tasks = [t["id"] for t in (tj.get("tasks") or [])]
        except Exception:
            all_tasks = []
        # 只挑近 120 条问（够用，且别把后端打爆）
        cand = products_of(all_tasks[-120:])
        print("  有产物的历史任务: %d 个（样本 %s）" % (len(cand), str(cand[:2])[:160]),
              flush=True)

        opened = False
        for tid, files in cand[:6]:
            hit = js("(function(){var els=Array.from(document.querySelectorAll("
                     "'#historyList .history-item'));"
                     "var e=els.find(function(x){return x.innerHTML.indexOf(%s)>=0;});"
                     "if(!e) return 'NOT_IN_DRAWER'; e.click(); return 'CLICKED';})()"
                     % json.dumps(tid))
            if hit != "CLICKED":
                continue
            # GUI 侧现在先画「⏳ 正在读取…」再请求，并做最多 3 次带退避重试
            # （后端忙时救命）。所以要等到"不再是读取中"才判定，
            # 否则会把「还在读」误记成「产物不显示」。
            fl = None
            t0 = time.time()
            while time.time() - t0 < 35:
                fl = js("(function(){var c=document.getElementById('fileList');"
                        "return {n:c.querySelectorAll('.file-item').length,"
                        "text:(c.innerText||'').slice(0,200)};})()")
                txt = str((fl or {}).get('text') or '')
                if (fl or {}).get('n', 0) > 0:
                    break
                if ('读取中' not in txt and '读取失败' not in txt):
                    break     # 已经给出终态（暂无文件 / 读取失败）
                time.sleep(2)
            fl = js("(function(){var c=document.getElementById('fileList');"
                    "return {n:c.querySelectorAll('.file-item').length,"
                    "text:(c.innerText||'').slice(0,200)};})()")
            n = (fl or {}).get("n", 0)
            log("history_task_products", n > 0,
                "任务 %s 预期 %d 个产物 → 侧边栏 %d 个 | %s"
                % (tid, len(files), n, str((fl or {}).get('text'))[:120]))
            opened = n > 0
            if opened:
                # ---------- 10) 点产物 → 预览区出内容 ----------
                js("(function(){var i=document.querySelector('#fileList .file-item');"
                   "if(i) i.click(); return 1;})()")
                time.sleep(3.5)
                pv = js("(function(){var c=document.getElementById('filePreviewContainer');"
                        "return {len:(c.innerHTML||'').length,"
                        "text:(c.innerText||'').trim().slice(0,150)};})()")
                log("product_preview", bool(pv and pv.get("len", 0) > 120),
                    "预览区 %s 字符 | %s" % ((pv or {}).get("len"), str((pv or {}).get("text"))[:110]))
                break
        if not opened:
            log("history_task_products", False,
                "试了 %d 个有产物的任务，侧边栏都没列出产物" % min(6, len(cand)))
            log("product_preview", False, "上一步未通过，跳过")

        # ---------- 11) 新建对话：同步长请求，轮询等结果 ----------
        # 先退出历史回看视图（点历史任务后界面停在历史态），并等后端把上一轮任务
        # 收尾 —— /new_conversation 要真的驱动浏览器开新对话页，后端正忙着别的任务
        # 时它会被拖很久（这属于后端忙，不是 GUI bug）。
        js("(function(){ if (typeof closeHistoryView === 'function') "
           "closeHistoryView(); return 1;})()")
        for _ in range(40):
            busy = js("(function(){ return (TASKS||[]).some(function(t){"
                      "return t.status==='running';}); })()")
            if busy is False or busy == "false":
                break
            time.sleep(3)
        r = click_button("新建对话")
        txt = wait_for_text(None, timeout=150, step=3.0)
        ok_new = ("全新对话" in (txt or "")) or ("独立对话" in (txt or ""))
        log("new_conversation", r == "CLICKED" and ok_new,
            "click=%s 等待 150s 后文本=%r" % (r, str(txt)[:220]))

        # ---------- 12) 输入框 Enter 发送（真实用户路径） ----------
        before = msg_state() or {}
        js("(function(){var e=document.getElementById('input');e.focus();"
           "e.value='巡检测试：请只回复 仙人掌OK';"
           "e.dispatchEvent(new Event('input',{bubbles:true}));"
           "e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));"
           "return 1;})()")
        # ---------- 13) 任务中「停止/插话」按钮可用 ----------
        # 必须在"任务真的在跑"的那一小段里采样：sendInput 是同步 setStatus('busy')
        # 的，但简单任务几秒就结束、按钮随之隐藏（此时显示 online 是**正确**行为）。
        # 旧写法在 Enter 后 13 秒才读，简单任务早跑完了 → 假 FAIL。
        busy_seen, first_vis = False, None
        t0 = time.time()
        while time.time() - t0 < 10:
            v = js("(function(){var s=document.getElementById('stopBtn'),"
                   "i=document.getElementById('insertBtn');"
                   "return {stop:!!s&&getComputedStyle(s).display,"
                   "insert:!!i&&getComputedStyle(i).display,"
                   "status:(document.getElementById('statusText')||{}).textContent};})()")
            if first_vis is None:
                first_vis = v
            if v and v.get("stop") != "none":
                busy_seen = True
                break
            time.sleep(0.6)
        time.sleep(3)
        after = msg_state() or {}
        entered = after.get("user", 0) > before.get("user", 0)
        log("enter_key_send", entered, "Enter 后 user 气泡 %s -> %s | %s"
            % (before.get("user"), after.get("user"), str(after.get("text"))[:140]))
        log("stop_insert_visible", busy_seen,
            "忙碌期见到 ⏹/💬 显示=%s | 首次观测=%s | 末次=%s"
            % (busy_seen, str(first_vis)[:90], str(v)[:90]))

        # ---------- 12b) 跑完一轮任务后，对话区不许出现内部协议报文 ----------
        # 用户原话：面板里全是 〔命令成功〕{"type":"system",...} / 〔识别指令〕
        # {"tool":"done"} / ExecutionResult(id='1', ...) 这种看不懂的东西。
        # 现在这些应该被折成「人话」行。
        time.sleep(8)
        logtxt = js("(document.getElementById('messages').innerText||'')")
        bad = [k for k in ('"type":"system"', '"tool":"done"', 'ExecutionResult(',
                           '[命令成功]', '[识别指令]', '[开始工具]', '@@@@')
               if k in str(logtxt)]
        log("no_protocol_after_task", not bad, "发现内部报文片段=%s" % bad)

        # ---------- 14) 任务列表 ----------
        tl = js("(function(){var e=document.getElementById('taskList');"
                "return {n:e?e.querySelectorAll('.task-item').length:null,"
                "text:(e?e.innerText:'').slice(0,160)};})()")
        log("tasklist_rendered", bool(tl and tl.get("n", 0) >= 1), "任务列表=%s" % str(tl)[:200])

        # ---------- 15) 删除任务走真接口（不做破坏性操作，只验契约） ----------
        # 用户当初抱怨的是「假删除」：只从前端数组里删，刷新就复活。
        # 这里只验证 (a) 代码路径确实打 DELETE /conversations/<id>，
        # (b) 该端点在假 id 上返回结构化 JSON 而不是 404/500 崩掉。
        wired = js("(function(){var s=String(removeTask);"
                   "return {conv:s.indexOf('/conversations/')>=0,"
                   "del:s.indexOf('DELETE')>=0};})()")
        try:
            req = urllib.request.Request(API + "/conversations/__xrz_probe_not_exist__",
                                         method="DELETE")
            resp = urllib.request.urlopen(req, timeout=15)
            body = resp.read().decode()[:200]
            try:
                j = json.loads(body)
                contract = isinstance(j, dict) and ("type" in j or "text" in j)
            except Exception:
                contract = False
        except urllib.error.HTTPError as e:
            # 404 + 结构化 JSON（"任务不存在"）是**正确**行为，不是失败：
            # 说明端点真的存在、真的按 id 查了、查不到就明确报错。
            raw = e.read().decode()[:200]
            body = "HTTP %s %s" % (e.code, raw)
            try:
                j = json.loads(raw)
                contract = isinstance(j, dict) and ("type" in j or "text" in j)
            except Exception:
                contract = False
        log("delete_is_real",
            bool(wired and wired.get("conv") and wired.get("del") and contract),
            "前端调用=%s 接口应答=%r" % (str(wired), body[:140]))

        # 留痕
        try:
            bridge("", kind="shot", path=os.path.join(DUMP, "audit_panel.png"), timeout=30)
            bridge("", kind="pdf", path=os.path.join(DUMP, "audit_panel.pdf"), timeout=45)
            print("  [留痕] audit_panel.png/.pdf -> gui_dumps/", flush=True)
        except Exception as e:
            print("  [留痕] 失败:", str(e)[:100], flush=True)
    finally:
        try:
            gui.terminate()
        except Exception:
            pass
        logf.close()

    npass = sum(1 for r in RESULTS if r["ok"])
    print("\n== 汇总: %d/%d PASS ==" % (npass, len(RESULTS)), flush=True)
    for r in RESULTS:
        if not r["ok"]:
            print("   FAIL:", r["name"], "|", r["detail"][:220], flush=True)
    json.dump(RESULTS, open(os.path.join(ROOT, "_gui_audit_result.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
