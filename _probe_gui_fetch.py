"""聚焦诊断：GUI 里点历史任务后，产物请求到底怎么了。

用性能条目（PerformanceResourceTiming）拿"页面自己发的那个请求"的真实
时长/状态码，区分三种可能：
  A) 请求根本没发出去（GUI 状态机卡住）
  B) 请求发了但一直 pending（浏览器侧）
  C) 请求很快回来但列表没渲染（渲染逻辑问题）
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
API = "http://127.0.0.1:8888"
BRIDGE = "http://127.0.0.1:9333"
TID = "deepseek_20260913_143112_10810"

import psutil


def bridge(payload, timeout=45):
    req = urllib.request.Request(BRIDGE + "/", data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode()).get("value")


def js(expr, timeout=45, tries=3):
    w = ("JSON.stringify((function(){ try { return (" + expr +
         "); } catch(e) { return {__err:String(e)}; } })())")
    for _ in range(tries):
        v = bridge({"js": w}, timeout=timeout)
        if v not in (None, "", "null"):
            try:
                return json.loads(v)
            except Exception:
                return v
        time.sleep(1)
    return None


def kill_stale():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info.get("name") in ("pythonw.exe", "python.exe") and \
               "desktop_app.py" in " ".join(p.info.get("cmdline") or []):
                p.kill()
        except Exception:
            pass


def port_open(port):
    s = socket.socket(); s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def main():
    kill_stale(); time.sleep(2)
    env = dict(os.environ)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(v, None)
    env.update({"XRZ_GUI_BRIDGE": "1", "PYTHONIOENCODING": "utf-8",
                "QTWEBENGINE_DISABLE_SANDBOX": "1",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu --disable-dev-shm-usage"})
    logf = open(os.path.join(ROOT, "_probe_load_app.log"), "w", encoding="utf-8")
    gui = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                           stdout=logf, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 150 and not port_open(9333):
            time.sleep(2)
        t0 = time.time()
        while time.time() - t0 < 120:
            if js("!!document.getElementById('input')"):
                break
            time.sleep(2)
        time.sleep(4)
        print("桥已连接", flush=True)

        # 打开历史抽屉，点目标任务
        js("(function(){ if (typeof toggleHistory==='function') toggleHistory(true); return 1;})()")
        time.sleep(2.5)
        print("点击任务:", TID, flush=True)
        print("  click =", js("(function(){var els=Array.from(document.querySelectorAll("
                            "'#historyList .history-item'));"
                            "var e=els.find(function(x){return x.innerHTML.indexOf(%s)>=0;});"
                            "if(!e) return 'NOT_FOUND'; e.click(); return 'CLICKED';})()"
                            % json.dumps(TID)), flush=True)

        # 立刻从页面里手工打一发同样的请求，测真实 RTT
        print("\n-- 页面内直接 fetch /attachments（同样参数）--", flush=True)
        js("(function(){ window.__t0=performance.now();"
           "window.__probe='pending';"
           "fetch('/attachments?conversation_id=' + encodeURIComponent(%s))"
           ".then(function(r){return r.text();})"
           ".then(function(t){ window.__probe='ok:'+t.length+'B'; })"
           ".catch(function(e){ window.__probe='err:'+e; });"
           "return 1;})()" % json.dumps(TID), timeout=20)
        for i in range(20):
            st = js("({probe:window.__probe, ms:Math.round(performance.now()-window.__t0),"
                    "conv:FILE_SCOPE_CONV, loading:FILE_LOADING, n:FILE_LIST.length,"
                    "err:FILE_LOAD_ERROR,"
                    "side:(document.getElementById('fileList').innerText||'').slice(0,60)})")
            print("  t=%.1fs %s" % (i * 1.0, str(st)[:200]), flush=True)
            if st and str(st.get("probe", "")).startswith(("ok:", "err:")) and i >= 4:
                break
            time.sleep(1)

        print("\n-- 页面资源计时（/attachments 的真实条目）--", flush=True)
        ent = js("(performance.getEntriesByType('resource')||[])"
                 ".filter(function(e){return e.name.indexOf('attachments')>=0;})"
                 ".map(function(e){return {d:Math.round(e.duration),"
                 "sz:e.transferSize,st:e.responseStatus,"
                 "url:e.name.slice(e.name.indexOf('/attachments'))};})", timeout=30)
        for e in (ent or [])[-8:]:
            print("  ", e, flush=True)
        print("\n-- 页面资源计时（全部慢请求 >1s）--", flush=True)
        slow = js("(performance.getEntriesByType('resource')||[])"
                  ".filter(function(e){return e.duration>1000;})"
                  ".map(function(e){return {d:Math.round(e.duration),"
                  "url:e.name.replace(location.origin,'').slice(0,80)};})", timeout=30)
        for e in (slow or [])[-12:]:
            print("  ", e, flush=True)
    finally:
        try:
            gui.terminate()
        except Exception:
            pass
        logf.close()


if __name__ == "__main__":
    main()
