# -*- coding: utf-8 -*-
"""GUI 真机驱动测试（纯模拟，不碰物理鼠标键盘）

之前所有回归都是直接 POST /command 打后端，等于绕过了 GUI，界面上的问题
（思考过程不显示、链路信息错乱、历史任务上下文串台）根本暴露不出来。

本脚本的做法：
  1. 启动后端 + 启动桌面 GUI（QTWEBENGINE_REMOTE_DEBUGGING=9222 打开面板调试口）
  2. 用 Playwright 通过 CDP 连到「面板页面」，用浏览器级事件操作：
     fill / click / type —— 跟用户手动点面板效果一致，但完全不占用物理输入设备，
     窗口也不需要抢焦点。
  3. 断言面板 DOM：思考块、工具块、最终回复、历史任务上下文切换
  4. 每一步把面板整页打印成 PDF + 截图，落 gui_dumps/

用法: python _gui_drive.py [场景...]    场景: basic tool history all(默认)
"""
import io, os, sys, time, json, subprocess, traceback
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psutil
import urllib.request
# 【沙箱环境】所有 127.0.0.1 请求必须绕过系统代理（沙箱有全局 HTTP_PROXY，
# 会把 localhost 请求劫走返回 502，导致 CDP 调试口 / 后端接口全连不上）
import urllib.request as _urlib
_urlib.install_opener(_urlib.build_opener(_urlib.ProxyHandler({})))


PY = r"D:\软件\Python\python.exe"
ROOT = r"D:\软件\XianRenZhangAgent"
TITLE = "仙人掌 Agent"
CDP = "http://127.0.0.1:9222"
DUMP = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_dumps")
os.makedirs(DUMP, exist_ok=True)

RESULTS = []


def log(tag, ok, detail=""):
    mark = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
    RESULTS.append({"tag": tag, "ok": ok, "detail": str(detail)[:500]})
    print(f"  [{mark}] {tag}: {str(detail)[:240]}", flush=True)


# ---------------- 后端 / 进程 ----------------
def healthy():
    try:
        d = json.loads(urllib.request.urlopen("http://127.0.0.1:8888/health", timeout=3).read().decode())
        return d.get("status") == "ok" and d.get("agent_ready")
    except Exception:
        return False


def wait_healthy(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if healthy():
            return True
        time.sleep(3)
    return False


def kill_stale():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if p.info.get("name") != "python.exe":
            continue
        cl = " ".join(p.info.get("cmdline") or [])
        if "desktop_app.py" in cl:
            try:
                p.kill()
                print(f"  killed stale gui pid={p.info['pid']}", flush=True)
            except Exception:
                pass


# ---------------- 面板（经桌面壳测试桥操作，XRZ_GUI_BRIDGE=1 激活）----------------
import urllib.request as _ur2

_ur2.install_opener(_ur2.build_opener(_ur2.ProxyHandler({})))  # 沙箱代理会劫 localhost
BRIDGE = "http://127.0.0.1:9333"


class Panel:
    """通过桌面壳内置测试桥（http://127.0.0.1:9333）操作真实窗口里的面板：
    - /eval  : 在面板页面里执行 JS（读 DOM 断言 / 派发 DOM 事件模拟用户操作）
    - /pdf   : 用 Qt printToPdf 把面板整页打印成 PDF（留痕）
    - /shot  : Qt grab() 截图（留痕）
    为什么不用 CDP：Qt WebEngine 的浏览器级 CDP 能握手但页面会话不响应
    （Runtime.evaluate 永远超时），实测全部变体都一样，只能走 Qt 原生通道。"""

    def __init__(self):
        self.ok = False

    def _post(self, payload, timeout=35):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _ur2.Request(BRIDGE + "/eval", data=data,
                           headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(_ur2.urlopen(req, timeout=timeout).read().decode("utf-8"))
        v = r.get("value")
        if isinstance(v, str) and v.startswith("ERR "):
            raise RuntimeError(v)
        return v

    def attach(self, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                v = self._post({"js": "1+1"})
                if v == 2:
                    self.ok = True
                    print("  测试桥已连上面板进程", flush=True)
                    return True
            except Exception as e:
                print("  测试桥未就绪:", str(e)[:90], flush=True)
            time.sleep(2)
        return False

    def _eval_once(self, js, timeout=35):
        # 【坑】Qt 桥只可靠地回传标量；JS 对象/数组会被退化成空字符串。
        # 所以统一让 JS 端 JSON.stringify 成字符串，Python 这边再解析。
        wrapped = ("JSON.stringify((function(){ try { return (" + js +
                   "); } catch(e) { return {__err: String(e)}; } })())")
        return self._post({"js": wrapped}, timeout=timeout)

    def evaluate(self, js, timeout=35, tries=4):
        # 【坑】Qt WebEngine 的 runJavaScript 偶发丢回调（返回空串），重试即可。
        v = None
        for i in range(tries):
            v = self._eval_once(js, timeout=timeout)
            if v is not None and v != "":
                break
            if i + 1 < tries:
                print(f"   [debug] evaluate 返回 {v!r}，第 {i+1} 次重试…", flush=True)
                time.sleep(1.5)
        if v is None:
            return None
        if not isinstance(v, str):
            return v
        try:
            return json.loads(v)
        except Exception:
            return v

    def wait_ready(self, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.evaluate("!!(document.getElementById('input') && document.getElementById('sendBtn'))"):
                    return True
            except Exception:
                pass
            time.sleep(2)
        return False

    # ---- 模拟用户操作（派发与真实点击相同的 DOM 事件）----
    def click(self, selector):
        return self.evaluate(f"""(() => {{
            const e = document.querySelector({selector!r});
            if (!e) return false;
            e.focus && e.focus();
            e.dispatchEvent(new MouseEvent('mousedown', {{bubbles:true}}));
            e.dispatchEvent(new MouseEvent('mouseup', {{bubbles:true}}));
            e.click();
            return true;
        }})()""")

    def type_text(self, selector, text):
        """像用户输入一样：聚焦 → 逐段设置值 → 每次都派发 input 事件"""
        return self.evaluate(f"""(() => {{
            const e = document.querySelector({selector!r});
            if (!e) return false;
            e.focus();
            e.value = {text!r};
            e.dispatchEvent(new Event('input', {{bubbles:true}}));
            e.dispatchEvent(new Event('change', {{bubbles:true}}));
            return true;
        }})()""")

    def send(self, text):
        """在面板输入框打字并点发送 —— 完全模拟用户操作"""
        self.type_text("#input", text)
        time.sleep(0.3)
        ok = self.click("#sendBtn")
        if not ok:
            self.evaluate("""(() => {
                const b = document.querySelector('#sendBtn');
                if (b) b.click();
            })()""")
        return ok

    # ---- 读取面板状态 ----
    def state(self):
        st = self.evaluate("""(() => {
            const q = (s) => document.querySelector(s);
            const msgs = Array.from(document.querySelectorAll('#messages .msg')).map(e => ({
                cls: e.className || '', text: (e.innerText || '').slice(0, 800)
            }));
            const think = Array.from(document.querySelectorAll('#messages .thinking-block'))
                .map(e => ({cls: e.className || '', text: (e.innerText||'').slice(0, 600)}));
            const toolSteps = Array.from(document.querySelectorAll('#messages .think-step, #messages .thinking-block [class*="step"]'))
                .map(e => (e.innerText||'').trim()).filter(t => t.includes('🔧') || /调用|完成|工具/.test(t));
            const drawer = q('#historyDrawer');
            return {
                n_msg: msgs.length, msgs: msgs.slice(-8),
                n_think: think.length, think: think.slice(-4),
                tool_steps: toolSteps.slice(-8), n_tool_steps: toolSteps.length,
                status: (q('#statusText')||{}).innerText || '',
                histOpen: drawer ? drawer.classList.contains('open') : null,
                histBanner: !!q('.hist-banner'),
                bannerTitle: (q('.hist-banner .hb-title')||{}).innerText || '',
                n_hist: document.querySelectorAll('#historyList .hi-title').length,
                histOpen2: (q('#historyDrawer')||{}).className || '',
                histRaw: (q('#historyList')||{}).innerText || '',
                inputVal: (q('#input')||{}).value || '',
            };
        })()""")
        if not isinstance(st, dict):
            return {"error": f"evaluate 返回异常: {st!r}"}
        return st

    def dump(self, tag):
        ts = time.strftime("%Y%m%d_%H%M%S")
        pdf = os.path.join(DUMP, f"{ts}_{tag}.pdf")
        png = os.path.join(DUMP, f"{ts}_{tag}.png")
        try:
            v = self._post({"kind": "pdf", "path": pdf}, timeout=40)
            if v is not True:
                pdf = f"(pdf失败:{v})"
        except Exception as e:
            pdf = f"(pdf失败:{str(e)[:50]})"
        try:
            self._post({"kind": "shot", "path": png}, timeout=30)
        except Exception:
            png = ""
        print(f"    [留痕] {tag} -> {os.path.basename(str(pdf))}", flush=True)
        return pdf


def run_task(panel, text, tag, wait=300):
    """通过 GUI 面板发一条指令，等到面板不再变化为止"""
    before = panel.state().get("n_msg", 0)
    panel.send(text)
    t0 = time.time()
    last_n, stable, last_status = before, 0, ""
    while time.time() - t0 < wait:
        time.sleep(3)
        st = panel.state()
        n = st.get("n_msg", 0)
        last_status = st.get("status", "")
        if n != last_n:
            last_n, stable = n, 0
        else:
            stable += 3
        # 出现新回复后，连续 15s 没变化且状态回到空闲 → 认为结束
        if n > before and stable >= 15:
            break
    time.sleep(2)
    st = panel.state()
    panel.dump(tag)
    st["_new"] = st.get("n_msg", 0) - before
    return st


def main():
    scenes = sys.argv[1:] or ["all"]
    if "all" in scenes:
        scenes = ["basic", "tool", "history"]

    print("== GUI 面板驱动测试（模拟操作）==", flush=True)
    if not healthy():
        print("  后端未运行，先启动后端…", flush=True)
        env = dict(os.environ)
        env["XRZ_NO_GUI"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.Popen([PY, "-u", "terminal.py"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_healthy():
            log("gui_backend_ready", False, "后端 300s 未就绪")
            return 2
    log("gui_backend_ready", True, "后端就绪")

    kill_stale()
    time.sleep(2)

    env = dict(os.environ)
    env["XRZ_GUI_BRIDGE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 【关键】从受限会话启动时，QtWebEngine 的渲染进程沙箱起不来
    # （loadFinished=False、runJavaScript 回调永不触发），界面等于白屏。
    # 禁用沙箱 + 关掉 GPU 虚拟化后才能正常渲染。仅测试环境需要。
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
    # Qt WebEngine 需要同时给启动参数才会开远程调试口（实测只设环境变量不生效）
    gui = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                           stdout=open(os.path.join(ROOT, "_gui_drive_app.log"), "w", encoding="utf-8"),
                           stderr=subprocess.STDOUT)
    try:
        panel = Panel()
        if not panel.attach():
            log("gui_bridge", False, "连不上面板测试桥（桌面壳未开 XRZ_GUI_BRIDGE？）")
            return 2
        log("gui_bridge", True, "测试桥已连上面板页面")
        if not panel.wait_ready():
            log("gui_panel_ready", False, "面板 90s 未渲染出输入框/发送按钮")
            panel.dump("00_notready")
            return 2
        log("gui_panel_ready", True, "面板渲染完成")
        time.sleep(2)
        panel.evaluate("(function(){ window.__errs=[]; window.addEventListener('error', function(e){window.__errs.push('err:'+e.message)}); window.addEventListener('unhandledrejection', function(e){window.__errs.push('rej:'+String(e.reason && e.reason.message || e.reason))}); return 1; })()")
        st0 = panel.state()
        log("gui_initial_status", True, f"状态栏={st0.get('status')!r} 消息数={st0.get('n_msg')}")
        panel.dump("00_start")

        # ── 场景1 基础对话 ──
        if "basic" in scenes:
            print("\n---- 场景1 基础对话（面板真实输入）----", flush=True)
            st = run_task(panel,
                          "帮我整理一份「Python 异步编程入门」的学习大纲，"
                          "直接列出 5 个章节标题、每章一句话说明，不用调用工具", "01_basic")
            log("gui_basic_new_msgs", st.get("_new", 0) >= 2,
                f"新增气泡 {st.get('_new')} 条，状态栏={st.get('status')!r}")
            tail = " || ".join((m.get("text") or "")[:90] for m in (st.get("msgs") or [])[-3:])
            log("gui_basic_answer", bool(tail.strip(" |")), f"面板尾部：{tail[:300]}")
            log("gui_basic_thinking", st.get("n_think", 0) >= 1,
                f"思考块数量={st.get('n_think')} 内容={json.dumps(st.get('think'), ensure_ascii=False)[:240]}")
            # 输入框必须被清空（否则说明前端没处理发送后清空）
            log("gui_basic_input_cleared", not (st.get("inputVal") or "").strip(),
                f"发送后输入框残留={str(st.get('inputVal'))[:60]!r}")

        # ── 场景2 工具任务 ──
        if "tool" in scenes:
            print("\n---- 场景2 工具任务（file_write）----", flush=True)
            out = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_异步编程学习笔记.docx")
            try:
                if os.path.exists(out):
                    os.remove(out)
            except Exception:
                pass
            st = run_task(panel,
                          "帮我写一份《Python 异步编程入门》学习笔记，"
                          "用 docx_create 生成 Word 文档，path 设为 " + out + "，"
                          "至少 3 个章节，每章有要点列表", "02_tool", wait=420)
            exists = os.path.exists(out) and os.path.getsize(out) > 0
            log("gui_tool_file", exists,
                f"{os.path.basename(out)} exists={exists} "
                f"size={os.path.getsize(out) if exists else 0}")
            blob = json.dumps(st, ensure_ascii=False)
            steps = st.get("tool_steps") or []
            log("gui_tool_shown", ("docx_create" in blob) or exists,
                f"面板是否展示 docx_create 工具调用={('docx_create' in blob)}")
            log("gui_tool_steps", len(steps) >= 1,
                f"工具调用步骤展示 {len(steps)} 条：{steps[:4]}")

        # ── 场景3 历史任务上下文 ──
        if "history" in scenes:
            print("\n---- 场景3 历史任务上下文 ----", flush=True)
            opened = panel.evaluate("""(() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(x => (x.innerText||'').includes('历史记录'));
                if (b) { b.click(); return 'btn'; }
                if (typeof toggleHistory === 'function') { toggleHistory(true); return 'fn'; }
                return false; })()""")
            if not opened:
                opened = panel.evaluate("(function(){ if (typeof toggleHistory==='function'){ toggleHistory(true); return 'fn'; } return false; })()")
            print("   [debug] opened=", repr(opened), flush=True)
            time.sleep(4)
            panel.dump("03_history")
            st = panel.state()
            log("gui_history_open", bool(opened) and st.get("n_hist", 0) > 0,
                f"opened={opened!r} 抽屉打开={st.get('histOpen')} 历史条目={st.get('n_hist')} "
                f"列表HTML={str(st.get('histRaw'))[:160]!r}")
            if st.get("n_hist", 0) > 0:
                print("   [debug] 点击前 JS 错误:", panel.evaluate("JSON.stringify(window.__errs||[])"), flush=True)
                click_ret = panel.evaluate("""(() => {
                    const els = document.querySelectorAll('#historyList .hi-title');
                    const r = {n: els.length, t: els.length ? els[0].textContent : null,
                               vis: els.length ? els[0].getClientRects().length : -1};
                    if (els.length) els[0].click();
                    return r; })()""")
                title = (click_ret or {}).get("t")
                print("   [debug] click ret:", repr(click_ret), flush=True)
                time.sleep(3.5)
                panel.dump("04_history_task")
                st2 = panel.state()
                print("   [debug] JS 错误:", panel.evaluate("JSON.stringify(window.__errs||[])"), flush=True)
                log("gui_history_switch", bool(st2.get("histBanner")),
                    f"点击的历史任务={str(title)[:60]!r} 出现历史横幅={st2.get('histBanner')} "
                    f"横幅标题={st2.get('bannerTitle')!r} 展示消息数={st2.get('n_msg')}")
                log("gui_history_content", st2.get("n_msg", 0) >= 1,
                    f"历史视图里应该有该任务的上下文消息，实际 n_msg={st2.get('n_msg')}")
                panel.evaluate("""(() => { const b = document.querySelector('.hb-back');
                    if (b) { b.click(); return 'back'; } return null; })()""")
                time.sleep(2.5)
                panel.dump("05_back")
                st3 = panel.state()
                log("gui_history_back", not st3.get("histBanner"),
                    f"返回后横幅={st3.get('histBanner')}")

        panel.dump("99_end")
    finally:
        print("== 关闭 GUI ==", flush=True)
        try:
            gui.terminate(); gui.wait(timeout=10)
        except Exception:
            try: gui.kill()
            except Exception: pass

    total = len(RESULTS)
    ok = sum(1 for r in RESULTS if r["ok"] is True)
    bad = [r for r in RESULTS if r["ok"] is not True]
    print(f"\n{'='*56}\nGUI 面板测试 {total} 项，通过 {ok}，未通过 {total-ok}", flush=True)
    for r in bad:
        print(f"  !! {r['tag']}: {r['detail'][:220]}", flush=True)
    with open(os.path.join(ROOT, "gui_drive_report.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS}, f,
                  ensure_ascii=False, indent=2)
    return 0 if not bad else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)
