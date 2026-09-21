"""
通过【真实 GUI 界面】(gui.html) 测试，而非绕过窗口直接打后端 API。
加载 http://127.0.0.1:8888/gui.html，点击真实的侧边栏平台按钮 / 输入框 / 发送按钮。

用法:
  python _gui_test.py <platform> <测试命令文本...>
  例:
    python _gui_test.py tongyi 你好
    python _gui_test.py doubao "@@@@{"tool":"file_write","content":"hi"}@@@@ 测试写文件"

  也支持不带命令只做切换+截图:
    python _gui_test.py deepseek --switch-only
"""
import sys, time, json, urllib.request, os
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8888"
SHOT = lambda name: os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def backend_health():
    try:
        with urllib.request.urlopen(B + "/health", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def wait_platform_ready(name, timeout=150):
    """轮询后端 /health 直到 platform==name 且 agent_ready。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = backend_health()
        if h.get("agent_ready") and h.get("platform") == name:
            return True, round(time.time() - t0, 1)
        time.sleep(2)
    return False, backend_health()


def main():
    platform = sys.argv[1]
    args = sys.argv[2:]
    switch_only = "--switch-only" in args
    if switch_only:
        args = [a for a in args if a != "--switch-only"]
    cmd = " ".join(args)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1240, "height": 800})

        print(f"[GUI] 加载 {B}/gui.html ...")
        page.goto(B + "/gui.html", wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)

        # 确认界面真的渲染了（输入框 + 平台侧边栏 + 状态点）
        has_input = page.locator("#input").count()
        has_plats = page.locator(".sidebar button[id^=pl-]").count()
        print(f"[GUI] 界面渲染: 输入框={has_input>0} 平台按钮数={has_plats}")

        # 等 agent 就绪（左下角状态变已连接）
        for _ in range(60):
            try:
                if page.locator("#statusDot.online").count() > 0:
                    break
            except Exception:
                pass
            page.wait_for_timeout(1000)
        print(f"[GUI] 切换前状态点: online={page.locator('#statusDot.online').count()>0}, "
              f"busy={page.locator('#statusDot.busy').count()>0}")

        # ── 点击真实侧边栏按钮切换平台 ──
        print(f"[GUI] 点击侧边栏 #{'-'+platform} ...")
        page.locator(f"#pl-{platform}").click()
        ok, info = wait_platform_ready(platform)
        print(f"[GUI] 平台切换 {platform}: ok={ok} info={info}")
        page.wait_for_timeout(2000)  # 让系统消息进面板

        if not ok:
            print("[GUI] 平台未就绪，仍尝试继续/截图")

        # 截图：切换后的界面
        page.screenshot(path=SHOT(f"_gui_after_switch_{platform}.png"))
        print(f"[GUI] 已截图 _gui_after_switch_{platform}.png")

        if not switch_only and cmd:
            # ── 走真实输入框 + 发送按钮 ──
            print(f"[GUI] 在 #input 输入并点 #sendBtn 发送: {cmd[:60]!r}")
            page.locator("#input").fill(cmd)
            page.locator("#sendBtn").click()
            page.wait_for_timeout(3000)

            # 捕获发送瞬间（含已入队消息 + 忙状态）
            page.screenshot(path=SHOT(f"_gui_sent_{platform}.png"))

            # 等 SSE 把 AI 回复推进面板：观察 #messages 里 .msg-* 数量增长
            # （注意：面板容器真实 id 是 "messages"，不是 msgDiv）
            MSG = "#messages .msg"
            SEL = ".msg-ai, .msg-system, .msg-error, .msg-log"
            def ai_msgs():
                try:
                    return page.locator(MSG).count()
                except Exception:
                    return -1

            def dump_msgs(n=8):
                try:
                    els = page.locator(MSG).all()
                    out = []
                    for e in els[-n:]:
                        cls = (e.get_attribute("class") or "").replace("msg", "").strip()
                        out.append(f"[{cls}] {e.inner_text()[:150].strip()}")
                    return out
                except Exception as ex:
                    return [f"DUMP-ERR {ex}"]

            before = ai_msgs()
            t0 = time.time()
            grew = False
            last_log = 0
            while time.time() - t0 < 240:
                now = ai_msgs()
                if now > before:
                    grew = True
                    break
                if time.time() - last_log > 20:
                    last_log = time.time()
                    print(f"  ...{int(time.time()-t0)}s 消息数={now} 面板尾: {dump_msgs(1)}")
                    page.wait_for_timeout(1500)
                else:
                    page.wait_for_timeout(1500)

            # 读取面板全部消息（按类别 + 文本）
            new_msgs = dump_msgs(10)
            print(f"[GUI] 面板消息增长: {before}->{ai_msgs()} grew={grew}")
            print(f"[GUI] 面板最近 10 条（含类别）:")
            for m in new_msgs:
                print("  •", m)
            # 任务列表状态（侧边栏“任务列表”：容器 id=taskList，条目 class=task-item）
            try:
                items = page.locator("#taskList .task-item").all()
                out = []
                for it in items[-4:]:
                    cls = (it.get_attribute("class") or "").replace("task-item", "").strip()
                    out.append(f"[{cls}] {it.inner_text().replace(chr(10), ' ')[:60]}")
                print(f"[GUI] 任务列表: {out}")
            except Exception:
                pass
            page.screenshot(path=SHOT(f"_gui_result_{platform}.png"))
            print(f"[GUI] 已截图 _gui_result_{platform}.png")

        browser.close()


if __name__ == "__main__":
    main()
