# -*- coding: utf-8 -*-
"""探针7：逐步加码，定位到底哪一步让 runJavaScript 拿不到返回值"""
import io, os, sys, time, json, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request as u
u.install_opener(u.build_opener(u.ProxyHandler({})))
import psutil

PY = r"D:\软件\Python\python.exe"
ROOT = r"D:\软件\XianRenZhangAgent"
LOG = os.path.join(ROOT, "_bridge_probe_app.log")


def kill_gui():
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if (p.info.get("name") or "") != "python.exe":
            continue
        if "desktop_app.py" in " ".join(p.info.get("cmdline") or []):
            try:
                p.kill()
            except Exception:
                pass


def ev(js, timeout=25):
    try:
        r = u.urlopen("http://127.0.0.1:9333/eval",
                      data=json.dumps({"js": js}, ensure_ascii=False).encode("utf-8"),
                      timeout=timeout)
        return r.read().decode("utf-8", "replace")
    except Exception as e:
        return f"EXC {type(e).__name__} {e}"


def wrap(js):
    return ("JSON.stringify((function(){ try { return (" + js +
            "); } catch(e) { return {__err: String(e)}; } })())")


kill_gui()
time.sleep(1.5)
env = dict(os.environ)
env["XRZ_GUI_BRIDGE"] = "1"
env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                        stdout=open(LOG, "w", encoding="utf-8"),
                        stderr=subprocess.STDOUT)
try:
    for i in range(40):
        if '"value": 2' in ev("1+1"):
            print("bridge ready")
            break
        time.sleep(2)

    cases = [
        ("1 最简", "() => { return 'A'; })()"),
        ("2 取按钮数", "() => { const btns = Array.from(document.querySelectorAll('button')); return btns.length; })()"),
        ("3 find 中文", "() => { const btns = Array.from(document.querySelectorAll('button')); const b = btns.find(x => (x.innerText||'').includes('历史记录')); return b ? 'found' : 'notfound'; })()"),
        ("4 find 英文", "() => { const btns = Array.from(document.querySelectorAll('button')); const b = btns.find(x => (x.innerText||'').includes('History')); return b ? 'found' : 'notfound'; })()"),
        ("5 click 第一个按钮", "() => { const b = document.querySelector('button'); if (b) b.click(); return 'clicked'; })()"),
        ("6 click 历史按钮", "() => { const btns = Array.from(document.querySelectorAll('button')); const b = btns.find(x => (x.innerText||'').includes('历史记录')); if (b) { b.click(); return 'btn'; } return 'nobtn'; })()"),
    ]
    for name, js in cases:
        print(f"[{name}] -> {ev(wrap(js))}")
        time.sleep(1.2)
finally:
    try:
        proc.terminate(); proc.wait(timeout=8)
    except Exception:
        pass
    kill_gui()
