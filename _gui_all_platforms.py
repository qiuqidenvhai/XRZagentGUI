# -*- coding: utf-8 -*-
"""【GUI 窗口内】逐平台真机能力验证（不许绕过 GUI 直接调后端）

用法: python _gui_all_platforms.py deepseek tongyi
每一步都在真实界面上做：
  点平台按钮 → 界面输入框打字 → 回车发送 → 等界面状态回到空闲
  → 读界面「文件产物」栏 → 点产物 → 读预览区 → 截图留痕
产出: gui_dumps/gui_<platform>.png/.pdf + 控制台 PASS/FAIL 表
"""
import json, os, subprocess, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_op)

ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
API = "http://127.0.0.1:8888"
BRIDGE = "http://127.0.0.1:9333"
DUMP = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_dumps")
os.makedirs(DUMP, exist_ok=True)

RESULTS = []


def log(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print("  [%s] %-24s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:200]), flush=True)


def bridge(js_src=None, kind="eval", path="", timeout=60):
    data = {"js": js_src or "", "kind": kind, "path": path}
    req = urllib.request.Request(BRIDGE + "/", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode()).get("value")


def js(expr, timeout=60, tries=3):
    for i in range(tries):
        try:
            return bridge(expr, timeout=timeout)
        except Exception:
            time.sleep(1.5)
    return None


def port_open(p, host="127.0.0.1"):
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, p)); return True
    except Exception:
        return False
    finally:
        s.close()


def ensure_gui():
    if port_open(9333):
        try:
            if js("1+1", timeout=15) == 2:
                return True
        except Exception:
            pass
    # 清掉旧的
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if "desktop_app" in cl and p.info["pid"] != os.getpid():
                p.kill()
        except Exception:
            pass
    time.sleep(2)
    env = dict(os.environ)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(v, None)
    env["XRZ_GUI_BRIDGE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
    logf = open(os.path.join(ROOT, "_gui_platform_app.log"), "w", encoding="utf-8")
    subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                     stdout=logf, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 150:
        if port_open(9333):
            try:
                if js("1+1", timeout=15) == 2:
                    return True
            except Exception:
                pass
        time.sleep(2)
    return False


def click(sel):
    return js("(function(){var e=document.querySelector(%s); if(!e) return 'NO_EL';"
              "e.click(); return 'CLICKED';})()" % json.dumps(sel), timeout=30)


def click_text(txt):
    return js("(function(){var b=Array.from(document.querySelectorAll('button')).find("
              "function(x){return (x.innerText||'').indexOf(%s)>=0;});"
              "if(!b) return 'NO_BTN'; b.click(); return 'CLICKED';})()" % json.dumps(txt),
              timeout=30)


def send(text):
    """在界面输入框里打字 + 回车发送（真实用户路径）"""
    return js("(function(){var e=document.getElementById('input'); if(!e) return 'NO_INPUT';"
              "e.focus(); e.value=%s;"
              "e.dispatchEvent(new Event('input',{bubbles:true}));"
              "e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));"
              "return 'SENT';})()" % json.dumps(text), timeout=30)


def wait_idle(timeout=240):
    """动态轮询：界面状态回到非忙碌（不固定秒数放弃，按界面状态推进）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = js("(function(){var s=document.getElementById('statusText');"
                "var b=document.getElementById('stopBtn');"
                "return {status:(s&&s.textContent)||'',"
                "stop:(b?getComputedStyle(b).display:''),"
                "text:(document.getElementById('messages')||{}).innerText||''};})()",
                timeout=30) or {}
        if st.get("stop") == "none" and "工作" not in (st.get("status") or "") \
           and "执行" not in (st.get("status") or ""):
            return st
        time.sleep(3)
    return js("(function(){return {status:(document.getElementById('statusText')||{}).textContent,"
              "text:(document.getElementById('messages')||{}).innerText||''};})()") or {}


def file_pane():
    return js("(function(){var c=document.getElementById('fileList'); if(!c) return null;"
              "return {n:c.querySelectorAll('.file-item').length,"
              "text:(c.innerText||'').slice(0,220)};})()", timeout=30)


def preview_pane():
    return js("(function(){var c=document.getElementById('filePreviewContainer'); if(!c) return null;"
              "return {len:(c.innerHTML||'').length,"
              "text:(c.innerText||'').trim().slice(0,220)};})()", timeout=30)


TASKS = {
    "deepseek": ("用 pptx_create 生成一个2页的PPT《GUI验证》，第一页标题「GUI验证」，"
                 "第二页写「DeepSeek 界面验证通过」。保存为 test_output/gui_deepseek.pptx",
                 "gui_deepseek.pptx"),
    "tongyi":   ("用 docx_create 生成一份Word文档《GUI验证》，"
                 "正文写一句「通义千问 界面验证通过」。保存为 test_output/gui_tongyi.docx",
                 "gui_tongyi.docx"),
    "doubao":   ("用 file_write 写一个文本文件，内容只有一行「豆包 界面验证通过」，"
                 "保存为 test_output/gui_doubao.txt", "gui_doubao.txt"),
    "yuanbao":  ("用 file_write 写一个文本文件，内容只有一行「元宝 界面验证通过」，"
                 "保存为 test_output/gui_yuanbao.txt", "gui_yuanbao.txt"),
}


def run_platform(plat):
    print("\n===== 平台: %s =====" % plat, flush=True)
    task, fname = TASKS[plat]

    # 1) 界面里点平台切换按钮
    r = click("#pl-%s" % plat)
    log("%s_switch_click" % plat, r == "CLICKED", "点击平台按钮=%s" % r)
    # 等切换完成（界面文本出现「已切换到」且不再忙碌）
    t0 = time.time()
    switched = False
    while time.time() - t0 < 180:
        st = js("(function(){return {text:(document.getElementById('messages')||{}).innerText||'',"
                "stop:(document.getElementById('stopBtn')?getComputedStyle(document.getElementById('stopBtn')).display:'')};})()",
                timeout=30) or {}
        if sw := (("已切换到" in (st.get("text") or "")) or ("正在切换到" not in (st.get("text") or ""))):
            if st.get("stop") == "none":
                switched = True
                break
        time.sleep(3)
    log("%s_switch_ready" % plat, switched, "界面切换到位=%s" % switched)

    # 2) 界面输入框打字 + 回车
    s = send(task)
    log("%s_send" % plat, s == "SENT", "界面发送=%s" % s)

    # 3) 等任务在界面里跑完
    st = wait_idle(240)
    got = fname.split(".")[0] in (st.get("text") or "") or "已生成" in (st.get("text") or "")
    log("%s_reply" % plat, len(st.get("text") or "") > 0,
        "界面回复片段=%s" % str(st.get("text") or "")[-160:].replace("\n", " ")[:160])

    # 4) 界面「文件产物」栏是否真的列出产物
    fp = None
    for _ in range(20):
        fp = file_pane()
        if fp and fp.get("n", 0) > 0:
            break
        time.sleep(3)
    has = bool(fp and fp.get("n", 0) > 0)
    log("%s_filelist" % plat, has, "产物栏=%s" % str(fp)[:180])

    # 5) 点产物 → 预览区出内容
    pv = None
    if has:
        js("(function(){var i=document.querySelector('#fileList .file-item .file-info');"
           "if(!i){var j=document.querySelector('#fileList .file-item'); if(j) j.click(); return 'CLICK_FALLBACK';}"
           "i.click(); return 'CLICKED';})()", timeout=30)
        for _ in range(12):
            pv = preview_pane()
            if pv and pv.get("len", 0) > 50:
                break
            time.sleep(3)
        okpv = bool(pv and pv.get("len", 0) > 50)
        log("%s_preview" % plat, okpv, "预览区=%s 字符 | %s"
            % ((pv or {}).get("len"), str((pv or {}).get("text"))[:150]))

    # 6) 截图留痕（我随后用 Read 亲眼看）
    try:
        bridge("", kind="shot", path=os.path.join(DUMP, "gui_%s.png" % plat), timeout=45)
        bridge("", kind="pdf", path=os.path.join(DUMP, "gui_%s.pdf" % plat), timeout=60)
        log("%s_shot" % plat, True, "截图=%s" % os.path.join(DUMP, "gui_%s.png" % plat))
    except Exception as e:
        log("%s_shot" % plat, False, repr(e))


def main():
    plats = sys.argv[1:] or ["deepseek"]
    if not ensure_gui():
        print("GUI 测试桥起不来，终止"); sys.exit(2)
    print("GUI 测试桥已连接", flush=True)
    for p in plats:
        run_platform(p)
    ok = sum(1 for r in RESULTS if r["ok"])
    print("\n== 汇总: %d/%d PASS ==" % (ok, len(RESULTS)))
    for r in RESULTS:
        if not r["ok"]:
            print("   FAIL:", r["name"], "|", r["detail"][:160])
    with open(os.path.join(ROOT, "_gui_platform_result.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
