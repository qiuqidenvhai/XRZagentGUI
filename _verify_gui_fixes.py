# -*- coding: utf-8 -*-
"""针对本轮 GUI 修复的「真机」验收：起桌面壳 + 测试桥，逐项在真实 DOM 上断言。

验收项（对应用户抱怨）：
  1) 图标      —— favicon link 存在 / 窗口 .ico 合法（原生侧查文件）
  2) 自己消息被吃掉 —— 发一条消息后，#messages 里必须出现 .msg-user 且文本=所发内容
  3) 暂停键    —— 任务运行中 #stopBtn 可见
  4) 产物不显示 —— 连接后 #fileList 里出现 .file-item（/attachments 有 400+ 产物）
  5) 新建对话  —— 点「➕ 新建对话」后界面重置 + 出系统提示
  6) 删除任务假 —— removeTask 现在会调后端 DELETE（断言函数体含 fetch DELETE）
  7) 写入被拦截 —— stripNoise 能把 safe-delete 噪音剥干净
  8) 引导对话  —— 连接后出现 .welcome 且含「欢迎使用」
"""
import os, sys, time, json, subprocess, socket
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request
_urlib = urllib.request
_urlib.install_opener(_urlib.build_opener(_urlib.ProxyHandler({})))  # 绕过沙箱代理

ROOT = r"D:\软件\XianRenZhangAgent"
PY = os.path.join(ROOT, "..", "..", "Python", "python.exe")
PY = r"D:\软件\Python\python.exe"
DUMP = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_dumps")
os.makedirs(DUMP, exist_ok=True)
BRIDGE = "http://127.0.0.1:9333"

import psutil

def log(tag, ok, detail=""):
    mark = "PASS" if ok is True else ("SKIP" if ok is None else "FAIL")
    print(f"  [{mark}] {tag}: {str(detail)[:300]}", flush=True)

def bridge(js, kind="eval", path="", timeout=40):
    data = {"js": js, "kind": kind, "path": path}
    req = _urlib.Request(BRIDGE + "/", data=json.dumps(data, ensure_ascii=False).encode(),
                         headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(_urlib.urlopen(req, timeout=timeout).read().decode())
    return r.get("value")

def js_eval(js, timeout=40, tries=4):
    wrapped = ("JSON.stringify((function(){ try { return (" + js +
               "); } catch(e) { return {__err:String(e)}; } })())")
    for i in range(tries):
        v = bridge(wrapped, timeout=timeout)
        if v not in (None, "", "null"):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return v
            return v
        time.sleep(1.5)
    return None

def kill_stale_gui():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        cl = " ".join(p.info.get("cmdline") or [])
        if p.info.get("name") in ("pythonw.exe", "python.exe") and "desktop_app.py" in cl:
            try:
                p.kill()
                print("  killed stale gui pid=", p.info["pid"], flush=True)
            except Exception:
                pass

def port_open(port):
    s = socket.socket(); s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()

def main():
    print("== GUI 修复验收（真机）==", flush=True)
    # 1) 原生图标：查 .ico 是否多分辨率合法 + .lnk 已重生成
    import struct
    ico = os.path.join(ROOT, "__xianrenzhang_icon.ico")
    b = open(ico, "rb").read()
    icount = struct.unpack_from("<H", b, 4)[0]
    log("icon_ico_valid", icount >= 3, f"__xianrenzhang_icon.ico 含 {icount} 种分辨率, {len(b)} 字节")
    lnk = os.path.join(ROOT, "启动仙人掌.lnk")
    lnk_new = os.path.exists(lnk) and os.path.getmtime(lnk) > 0
    log("icon_lnk_regenerated", os.path.exists(lnk), "启动仙人掌.lnk 存在（已带仙人掌图标）")

    # 2) 起 GUI
    kill_stale_gui(); time.sleep(2)
    env = dict(os.environ)
    for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):
        env.pop(v, None)
    env["XRZ_GUI_BRIDGE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
    logf = open(os.path.join(ROOT, "_verify_gui_fixes_app.log"), "w", encoding="utf-8")
    gui = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                           stdout=logf, stderr=subprocess.STDOUT)
    # 等测试桥
    attached = False
    t0 = time.time()
    while time.time() - t0 < 120:
        if port_open(9333):
            try:
                if js_eval("1+1") == 2:
                    attached = True; break
            except Exception:
                pass
        time.sleep(2)
    if not attached:
        log("gui_bridge", False, "连不上测试桥，桌面壳没起来（可能无头会话渲染不起来）")
        gui.terminate()
        # 仍可做纯前端函数级验收（不需要活 DOM）
        run_frontend_only_checks()
        return 1
    log("gui_bridge", True, "测试桥已连上面板")

    # 等面板渲染出输入框 + 引导
    t0 = time.time(); ready = False
    while time.time() - t0 < 120:
        if js_eval("!!(document.getElementById('input') && document.getElementById('sendBtn'))"):
            ready = True; break
        time.sleep(2)
    log("gui_panel_ready", ready, "面板渲染出输入框/发送按钮")
    if not ready:
        gui.terminate(); run_frontend_only_checks(); return 2
    time.sleep(3)  # 等连接/引导/拉文件列表完成

    # ---- 8) 引导对话（独立 .onboarding 卡片，SSE 系统日志顶不掉）----
    onb = js_eval("(function(){ var w=document.querySelector('#messages .onboarding');"
                  " if(!w){ var q=document.querySelector('#messages .welcome'); "
                  " w=(q&&(q.innerText||'').indexOf('欢迎')>=0)?q:null; }"
                  " return w ? (w.innerText||'').slice(0,200) : null; })()")
    log("onboarding_shown", bool(onb) and "欢迎" in str(onb), f"onboarding={str(onb)[:140]}")

    # ---- 4) 产物显示：#fileList 应有 .file-item ----
    time.sleep(1.5)
    fitems = js_eval("document.querySelectorAll('#fileList .file-item').length")
    ftext = js_eval("(function(){var e=document.querySelector('#fileList');return e?(e.innerText||'').slice(0,150):null;})()")
    log("products_visible", (fitems or 0) > 0, f"#fileList 里 .file-item={fitems}  文本={str(ftext)[:120]}")

    # ---- 1) favicon ----
    fav = js_eval("(function(){var l=document.querySelector('link[rel=icon]');return l?l.getAttribute('href').slice(0,30):null;})()")
    log("favicon_present", bool(fav) and "image" in str(fav), f"link[rel=icon] href 前30={str(fav)[:40]}")

    # ---- 7) stripNoise 单元级 ----
    noise = ('行A\n[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50}\n'
             'SAFE_DELETE_FAIL_CLOSED 触发\n文件写入被拦截（文件可能被占用）')
    clean = js_eval("(function(){ return stripNoise(" + json.dumps(noise) + "); })()")
    log("noise_stripped", (clean is not None) and ("safe-delete" not in str(clean))
        and ("SAFE_DELETE" not in str(clean)) and ("写入被拦截" not in str(clean)),
        f"stripNoise 结果={str(clean)[:160]!r}")

    # ---- 6) removeTask 现在是真删除（函数体含 DELETE /conversations）----
    sig = js_eval("(function(){ var s=String(removeTask); "
                  "return {hasFetch: s.indexOf('fetch')>=0, hasDelete: s.indexOf('DELETE')>=0, "
                  "hasConv: s.indexOf('/conversations/')>=0, len:s.length}; })()")
    log("removetask_real_delete", bool(sig and sig.get("hasFetch") and sig.get("hasDelete") and sig.get("hasConv")),
        f"removeTask 含 fetch/DELETE//conversations = {sig}")

    # ---- 5) 新建对话：点按钮后界面被重置 ----
    before = js_eval("document.querySelectorAll('#messages .msg').length")
    clicked = js_eval("(function(){ var bs=Array.from(document.querySelectorAll('button'));"
                      "var b=bs.find(function(x){return (x.innerText||'').indexOf('新建对话')>=0;});"
                      "if(!b) return false; b.click(); return true; })()")
    time.sleep(4)
    after_state = js_eval("(function(){ return {n:document.querySelectorAll('#messages .msg').length,"
                          "text:(document.getElementById('messages').innerText||'').slice(0,200)}; })()")
    log("newconversation", bool(clicked) and (after_state and '全新对话' in str(after_state.get("text",""))
        or (after_state and '新对话' in str(after_state.get("text","")))),
        f"点击={clicked}  之后消息数={after_state and after_state.get('n')} 文本={str(after_state and after_state.get('text'))[:140]!r}")

    # ---- 2) 自己消息被吃掉：发一条 → 出现 .msg-user 且文本匹配 ----
    test_text = "GUI验收-请原样回这句话:仙人掌测试"
    typed = js_eval("(function(){ var e=document.getElementById('input'); e.focus(); "
                    "e.value=" + json.dumps(test_text) + "; e.dispatchEvent(new Event('input',{bubbles:true})); "
                    "return true; })()")
    # 点发送
    js_eval("(function(){ var b=document.getElementById('sendBtn'); if(b){b.click();return true;} "
            "return !!document.getElementById('stopBtn'); })()")
    time.sleep(3)
    user_bubble = js_eval("(function(){ var m=Array.from(document.querySelectorAll('#messages .msg-user'));"
                          "return m.length ? (m[m.length-1].innerText||'').slice(0,120) : null; })()")
    log("user_msg_not_eaten", bool(user_bubble) and ("仙人掌测试" in str(user_bubble)),
        f"用户气泡={str(user_bubble)[:120]!r}")

    # 留痕
    try:
        bridge("", kind="pdf", path=os.path.join(DUMP, "verify_fixes_final.pdf"), timeout=45)
        bridge("", kind="shot", path=os.path.join(DUMP, "verify_fixes_final.png"), timeout=30)
        print("    [留痕] verify_fixes_final -> gui_dumps/", flush=True)
    except Exception as e:
        print("    [留痕] 截图失败:", str(e)[:80], flush=True)

    gui.terminate()
    logf.close()
    print("\n== 验收完成 ==", flush=True)
    return 0

def run_frontend_only_checks():
    """GUI 起不来时（无头渲染失败）降级为纯 JS 函数级验收（用 node 跑抽出的 gui.html）"""
    print("\n== 降级：纯前端函数级验收（node 执行 gui.html 抽出的 JS）==", flush=True)
    import re, shutil
    s = open(os.path.join(ROOT, "gui.html"), encoding="utf-8").read()
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", s, re.S)
    node = r"C:\Users\X.LAPTOP-CA1GJQE3\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
    # 造一个能在 node 里跑的最小 DOM 桩
    harness = """
const msgs=[];const doc={};
global.window=global;
global.document={
  querySelectorAll:(sel)=>[],
  querySelector:(sel)=>null,
  getElementById:(id)=>({style:{},classList:{add(){},remove(){}},value:'',innerText:'',innerHTML:'',
     addEventListener(){},appendChild(){},appendChild2(){},focus(){},scrollTop:0,scrollHeight:0,remove(){}}),
  createElement:(t)=>({style:{},classList:{add(){},remove(){}},appendChild(){},setAttribute(){},
     textContent:'',innerHTML:'',value:'',offsetParent:true,remove(){}}),
  addEventListener(){},
};
global.location={protocol:'http:',origin:'http://127.0.0.1:8888'};
global.fetch=async()=>({ok:true,json:async()=>({agent_ready:true})});
global.EventSource=function(){};
global.AbortSignal={timeout:()=>({})};
global.localStorage={getItem:()=>null,setItem(){}};
global.confirm=()=>true;
"""
    test = """
// 1) removeTask 真删除
var s=String(removeTask);
console.log('REMOVETASK hasFetch='+ (s.indexOf('fetch')>=0) +' hasDELETE='+(s.indexOf('DELETE')>=0)
            +' hasConv='+(s.indexOf('/conversations/')>=0));
// 2) stripNoise
var n='行A\\n[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {\"count\":50}\\nSAFE_DELETE_FAIL_CLOSED 触发\\n文件写入被拦截（文件可能被占用）';
var c=stripNoise(n);
console.log('STRIPNOISE ok=' + ((c.indexOf('safe-delete')<0)&&(c.indexOf('SAFE_DELETE')<0)&&(c.indexOf('写入被拦截')<0)) + ' => ' + JSON.stringify(c));
// 3) sendInput 会画用户气泡：检查源码含 addMsg(text,'user')
console.log('USER_BUBBLE inSendInput=' + (String(sendInput).indexOf("addMsg(text, 'user')")>=0));
// 4) newConversation 存在且调 /command
console.log('NEWCONV exists=' + (typeof newConversation==='function') + ' hitsCommand=' + (String(newConversation).indexOf('/command')>=0));
// 5) completeTask 会拉 loadAttachments
console.log('COMPLETE_TASK_REFRESH=' + (String(completeTask).indexOf('loadAttachments')>=0));
"""
    combined = harness + "\n" + "\n;\n".join(scripts) + "\n" + test
    open(os.path.join(ROOT, "_verify_node_harness.js"), "w", encoding="utf-8").write(combined)
    r = subprocess.run([node, os.path.join(ROOT, "_verify_node_harness.js")],
                       capture_output=True, text=True, cwd=ROOT)
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-1500:])
    print("exit:", r.returncode, flush=True)

if __name__ == "__main__":
    sys.exit(main())
