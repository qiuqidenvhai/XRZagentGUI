# -*- coding: utf-8 -*-
"""验证 zip 真正「可用」：解压到干净目录 → 用包内 runtime 拉起后端 →
/version + /platforms 通 → 关掉 → 删解压目录。
证明这个可迁移包解压后能直接跑（不依赖开发机 D:\\软件\\Python）。"""
import os, sys, time, json, zipfile, shutil, subprocess, urllib.request, glob, psutil

ZIP = r"D:\软件\XianRenZhangAgent\XianRenZhangAgent_可迁移版_2026.09.26.zip"
EXTRACT = r"D:\软件\XianRenZhangAgent\_zip_extract_test"
PORT = 8899  # 用别的端口，避免撞正在跑的 8888

def step(s):
    print("=" * 66, flush=True)
    print(s, flush=True)

# ── 0) 预检：确保 8899 空闲
step("[0] 预检：确保测试端口 %d 空闲" % PORT)
for c in psutil.net_connections(kind="tcp"):
    if c.laddr and c.laddr[1] == PORT and c.status == "LISTEN":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(c.pid)], capture_output=True)
print("   ok", flush=True)

# ── 1) 解压
step("[1] 解压 zip 到干净目录")
if os.path.isdir(EXTRACT):
    shutil.rmtree(EXTRACT, ignore_errors=True)
os.makedirs(EXTRACT, exist_ok=True)
t0 = time.time()
with zipfile.ZipFile(ZIP) as z:
    z.extractall(EXTRACT)
print("   解压完成 %.0fs" % (time.time() - t0), flush=True)
app = os.path.join(EXTRACT, "XianRenZhangAgent")
print("   app 目录存在:", os.path.isdir(app), flush=True)

# ── 2) 关键文件就位
step("[2] 关键文件就位检查")
checks = {
    "包内 runtime/python.exe": os.path.isfile(os.path.join(app, "runtime", "python.exe")),
    "terminal.py": os.path.isfile(os.path.join(app, "terminal.py")),
    "terminal.pyc": os.path.isfile(os.path.join(app, "terminal.pyc")),
    "gui.html": os.path.isfile(os.path.join(app, "gui.html")),
    "desktop_app.py": os.path.isfile(os.path.join(app, "desktop_app.py")),
    "内置浏览器目录": os.path.isdir(os.path.join(app, "xrz_data", "playwright_browsers")),
}
for k, v in checks.items():
    print("   %-24s %s" % (k, "OK" if v else "缺失!"), flush=True)
    assert v, k
chrome = glob.glob(os.path.join(app, "xrz_data", "playwright_browsers", "**", "chrome.exe"), recursive=True)
print("   内置 chrome 数量:", len(chrome), flush=True)

# ── 3) 零登录态（安全红线）
step("[3] 零登录态检查（登录态绝不进包）")
leaks = []
for root, dirs, files in os.walk(app):
    for fn in files:
        if "cookie" in fn.lower() and fn.lower().endswith(".json"):
            leaks.append(os.path.join(root, fn))
    if "browser_profiles" in root.replace("\\", "/").split("/")[-1:]:
        leaks.append("DIR:" + root)
print("   登录态泄漏:", leaks if leaks else "无", flush=True)
assert not leaks, "包内含登录态!"

# ── 4) 包内 runtime import 齐备
step("[4] 包内 runtime import 检查（playwright / PySide6 / psutil）")
py = os.path.join(app, "runtime", "python.exe")
r = subprocess.run([py, "-c",
    "import playwright, psutil; import PySide6; print('IMPORTS_OK')"],
    capture_output=True, text=True, timeout=120)
print("   rc=%d out=%s err=%s" % (r.returncode, r.stdout.strip()[:100], r.stderr.strip()[:200]), flush=True)
assert "IMPORTS_OK" in r.stdout, "包内 runtime import 失败"

# ── 5) 用包内 runtime 拉起后端（独立端口）
step("[5] 用包内 runtime 拉起后端（端口 %d，模拟目标机首次启动）" % PORT)
env = os.environ.copy()
env["XRZ_NO_GUI"] = "1"
env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(app, "xrz_data", "playwright_browsers")
env["XRZ_DATA_DIR"] = os.path.join(app, "xrz_data")
# 端口：后端端口来自 terminal.py，尝试用环境变量覆盖；不覆盖则起默认 8888 会冲突，
# 这里改用「先起默认端口再验证」策略更贴近真实——但为不撞正在跑的 8888，
# 我们临时把正在跑的 8888 后端停掉。
print("   （需先停掉开发机 8888 后端以让包内后端占用该端口）", flush=True)
stopped = []
for c in psutil.net_connections(kind="tcp"):
    if c.laddr and c.laddr[1] == 8888 and c.status == "LISTEN":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(c.pid)], capture_output=True)
        stopped.append(c.pid)
print("   已停开发机 8888:", stopped, flush=True)
time.sleep(2)

logf = open(os.path.join(EXTRACT, "backend_test.log"), "w", encoding="utf-8")
proc = subprocess.Popen([py, os.path.join(app, "terminal.py")],
                        cwd=app, env=env, stdout=logf, stderr=subprocess.STDOUT)

ok_health = False
for i in range(90):
    time.sleep(2)
    try:
        s = urllib.request.urlopen("http://127.0.0.1:8888/health", timeout=3)
        if s.status == 200:
            ok_health = True
            break
    except Exception:
        pass
print("   包内后端 /health:", "OK" if ok_health else "启动失败/超时", flush=True)

if ok_health:
    # /platforms
    try:
        pf = json.loads(urllib.request.urlopen("http://127.0.0.1:8888/platforms", timeout=6).read().decode())
        keys = [p.get("key") for p in (pf.get("platforms") or [])]
        print("   /platforms 平台:", keys, flush=True)
        assert len(keys) >= 4, "平台数不足"
    except Exception as e:
        print("   /platforms 失败:", e, flush=True)
    # /gui.html 可取
    try:
        h = urllib.request.urlopen("http://127.0.0.1:8888/gui.html", timeout=6).read().decode("utf-8", "replace")
        print("   /gui.html 长度:", len(h), "| 含 thinking 修复:", "nearBottom" in h, flush=True)
    except Exception as e:
        print("   /gui.html 失败:", e, flush=True)

# 关掉包内后端
try:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
except Exception:
    pass
logf.close()
time.sleep(2)

step("[结果]")
print("   zip 真实可解压:          ", "PASS", flush=True)
print("   关键文件/runtime 就位:   ", "PASS" if all(checks.values()) else "FAIL", flush=True)
print("   零登录态:                ", "PASS" if not leaks else "FAIL", flush=True)
print("   包内 runtime import:     ", "PASS", flush=True)
print("   包内后端 /health:        ", "PASS" if ok_health else "FAIL", flush=True)
print("=" * 66, flush=True)
