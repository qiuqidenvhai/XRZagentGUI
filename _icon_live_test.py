# -*- coding: utf-8 -*-
"""图标真机自测：双击 .lnk 启动真实桌面应用 → 全屏截图 + 裁任务栏/窗口标题栏，
亲眼确认任务栏 & 标题栏显示的是仙人掌（非空白）。同时经测试桥截图窗口内部。"""
import os, sys, time, json, subprocess, ctypes, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_urlib = urllib.request
_urlib.install_opener(_urlib.build_opener(_urlib.ProxyHandler({})))

ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
OUT = os.path.join(ROOT, "_icon_live")
os.makedirs(OUT, exist_ok=True)

def say(*a):
    print(*a, flush=True)

def kill_old_guis():
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        cl = " ".join(p.info.get("cmdline") or [])
        if p.info.get("name") in ("pythonw.exe", "python.exe") and "desktop_app.py" in cl:
            try:
                p.kill(); say("killed old gui pid", p.info["pid"])
            except Exception:
                pass

def backend_ok():
    try:
        d = json.loads(_urlib.urlopen("http://127.0.0.1:8888/health", timeout=3).read().decode())
        return bool(d.get("agent_ready"))
    except Exception:
        return False

def main():
    say("== 图标真机自测 ==")
    kill_old_guis(); time.sleep(2)
    say("backend agent_ready:", backend_ok())

    # 1) 双击启动真实应用（.lnk 内嵌仙人掌图标 → 任务栏走 .lnk 图标）
    lnk = os.path.join(ROOT, "启动仙人掌.lnk")
    say("启动 .lnk:", lnk, os.path.exists(lnk))
    os.startfile(lnk)  # 等价于用户双击

    # 2) 同时带测试桥再起一个壳做窗口内截图（XRZ_GUI_BRIDGE=1）
    env = dict(os.environ)
    for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(v, None)
    env["XRZ_GUI_BRIDGE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-dev-shm-usage"
    # 等 .lnk 那个壳把 8888 占用逻辑消化：它发现后端已健体会跳过启动
    time.sleep(8)
    bridge = subprocess.Popen([PY, "-u", "desktop_app.py"], cwd=ROOT, env=env,
                              stdout=open(os.path.join(OUT, "bridge_app.log"), "w", encoding="utf-8"),
                              stderr=subprocess.STDOUT)

    def bridge_post(payload, timeout=35):
        import socket
        s = socket.create_connection(("127.0.0.1", 9333), timeout=3)
        s.sendall((json.dumps(payload, ensure_ascii=False)).encode())
        s.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        return json.loads(data.decode()).get("value")

    # 3) 等面板渲染
    t0 = time.time(); ready = False
    while time.time() - t0 < 150:
        try:
            if bridge_post({"js": "1+1"}) == 2:
                if bridge_post({"js": "!!(document.getElementById('input')&&document.getElementById('sendBtn'))"}) == True:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(3)
    say("bridge panel ready:", ready)

    # 4) 窗口内部截图（含自定义标题栏的仙人掌 QLabel）
    if ready:
        time.sleep(3)
        p1 = os.path.join(OUT, "window_shot.png")
        v = bridge_post({"kind": "shot", "path": p1}, timeout=30)
        say("window shot:", v, os.path.exists(p1))
        # 标题栏图标 QLabel 是否成功加载了 pixmap（空白则回退 emoji 🌵）
        info = bridge_post({"js": "JSON.stringify({titleText:(document.title||''), onb: !!document.querySelector('.onboarding'),"
                                  " prod: document.querySelectorAll('#fileList .file-item').length,"
                                  " userMsg: document.querySelectorAll('#messages .msg-user').length,"
                                  " stopBtnExists: !!document.getElementById('stopBtn')})"})
        say("panel state:", info)

    # 5) 全屏截图（看 Windows 任务栏上的图标）
    time.sleep(6)
    try:
        from PIL import Image, ImageGrab
        full = ImageGrab.grab()
        full.save(os.path.join(OUT, "desktop_full.png"))
        W, H = full.size
        # 裁任务栏（屏幕底部 56px，全宽）
        taskbar = full.crop((0, H - 56, W, H))
        taskbar.save(os.path.join(OUT, "taskbar.png"))
        # 任务栏图标一般集中在左侧；再裁左 1/3 放大 3 倍方便看清
        left = full.crop((0, H - 56, max(W // 3, 300), H))
        left.resize((left.width * 3, 56 * 3), Image.LANCZOS).save(os.path.join(OUT, "taskbar_left_3x.png"))
        say("desktop capture:", W, "x", H)
    except Exception as e:
        say("desktop capture failed:", type(e).__name__, str(e)[:200])

    # 6) 统计任务栏区域里「绿色像素」占比（仙人掌是绿的；空白/默认图标基本没有绿）
    try:
        from PIL import Image
        img = Image.open(os.path.join(OUT, "taskbar.png")).convert("RGB")
        px = img.load()
        w, h = img.size
        green = 0; total = w * h
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                if g > 90 and g > r * 1.3 and g > b * 1.3:
                    green += 1
        say(f"taskbar green-pixel ratio: {green}/{total} = {green/total:.4%}")
    except Exception as e:
        say("green check failed:", e)

    say("== done, 截图在", OUT, "==")

if __name__ == "__main__":
    main()
