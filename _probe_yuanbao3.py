# -*- coding: utf-8 -*-
"""元宝第三轮：抓模型下拉的完整结构（含模型子菜单），并验证切到「快速回答」。"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

PROFILE = r"D:\软件\XianRenZhangAgent\xrz_data\.xianrenzhang_agent\browser_profiles\yuanbao"
URL = "https://yuanbao.tencent.com/chat/"
DUMP = r"D:\软件\XianRenZhangAgent\probe_yuanbao"
os.makedirs(DUMP, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers"

out = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.append(s)


POP = "div[class*='z-[2500]']"

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE, headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1400, "height": 950},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)

    # 打开下拉
    page.locator("button:has-text('深度思考'), button:has-text('快速回答')").last.click(timeout=5000)
    time.sleep(2)
    html = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('div')).filter(e =>
            (e.className||'').toString().includes('z-[2500]'));
        return els.map(e => e.outerHTML).join('\\n<!--SEP-->\\n');
    }""")
    open(os.path.join(DUMP, "popover.html"), "w", encoding="utf-8").write(html)
    log("弹层 HTML 长度:", len(html))
    log(html[:3000])

    # 点「Hy3」看能不能展开模型子菜单
    try:
        page.locator(POP).last.locator("text=Hy3").first.click(timeout=4000)
        time.sleep(2)
        page.screenshot(path=os.path.join(DUMP, "20_model_sub.png"))
        sub = page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll('div')).filter(e =>
                (e.className||'').toString().includes('z-[2500]'));
            return els.map(e => (e.innerText||'').trim()).join(' | ');
        }""")
        log("点 Hy3 后弹层内容:", sub[:800])
    except Exception as e:
        log("点 Hy3 失败:", str(e)[:120])

    # 重新打开下拉并切到「快速回答」
    try:
        page.keyboard.press("Escape"); time.sleep(1)
        page.locator("button:has-text('深度思考'), button:has-text('快速回答')").last.click(timeout=5000)
        time.sleep(2)
        page.locator("text=快速回答").last.click(timeout=4000)
        time.sleep(2)
        page.screenshot(path=os.path.join(DUMP, "21_quick.png"))
        bar = page.evaluate("""() => {
            const b = document.querySelector('[class*=new-framework-input_searchContent]');
            return b ? (b.innerText||'').slice(0,200) : '';
        }""")
        log("切「快速回答」后底部栏:", repr(bar[:160]))
    except Exception as e:
        log("切快速回答失败:", str(e)[:120])

    page.screenshot(path=os.path.join(DUMP, "22_final.png"))
    ctx.close()

open(os.path.join(DUMP, "report3.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
