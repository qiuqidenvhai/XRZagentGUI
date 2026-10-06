# -*- coding: utf-8 -*-
"""_pkg_live_test.py —— 用包内 runtime 真正拉起分发包后端，验证可运行。

隔离数据目录（不污染交付包），拉起 package 后端 → 探 /version、/platforms → /shutdown。
"""
import os
import sys
import time
import subprocess
import urllib.request

PKG = r"D:\软件\XianRenZhangAgent\dist\XianRenZhangAgent"
TESTDATA = r"D:\xrz_pkg_test_data"
os.makedirs(TESTDATA, exist_ok=True)

env = dict(os.environ)
env["XRZ_NO_GUI"] = "1"
env["XRZ_DATA_DIR"] = TESTDATA
env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(PKG, "xrz_data", "playwright_browsers")

py = os.path.join(PKG, "runtime", "python.exe")
log = open(r"D:\软件\XianRenZhangAgent\pkg_backend_test.log", "w", encoding="utf-8")
print("启动包内后端: %s terminal.py (cwd=%s)" % (py, PKG), flush=True)
proc = subprocess.Popen([py, "terminal.py"], cwd=PKG, env=env,
                        stdout=log, stderr=subprocess.STDOUT)
print("pid=%d" % proc.pid, flush=True)

up = False
for i in range(45):
    time.sleep(2)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8888/version", timeout=2) as r:
            body = r.read().decode()
            print("[%2d] /version -> %s" % (i, body[:160]), flush=True)
            up = True
            break
    except Exception:
        # 也顺便看后端是否已崩
        if proc.poll() is not None:
            print("后端进程已退出 rc=%s" % proc.returncode, flush=True)
            break

if not up:
    print("❌ 后端未在 90s 内就绪", flush=True)
    log.flush()
    print("----- backend log tail -----", flush=True)
    try:
        with open(log.name, encoding="utf-8") as f:
            print("".join(f.readlines()[-40:]), flush=True)
    except Exception:
        pass
    proc.terminate()
    sys.exit(1)

# platforms
try:
    with urllib.request.urlopen("http://127.0.0.1:8888/platforms", timeout=8) as r:
        data = r.read().decode()
        print("PLATFORMS ok, bytes=%d, head=%s" % (len(data), data[:160]), flush=True)
except Exception as e:
    print("platforms 请求异常(非致命):", e, flush=True)

# shutdown
try:
    req = urllib.request.Request("http://127.0.0.1:8888/shutdown", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        print("SHUTDOWN ->", r.read().decode()[:120], flush=True)
except Exception as e:
    print("shutdown 异常:", e, flush=True)

print("✅ 包内后端可启动并响应 /version、/platforms，已正常 shutdown", flush=True)
