# -*- coding: utf-8 -*-
"""元宝第二轮探测：点「深度思考 ∨」下拉，抓模型/模式选项。"""
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


def dump_popovers(page, tag):
    got = page.evaluate("""() => {
        const res = [];
        const sel = '[class*=pop],[class*=menu],[class*=drop],[class*=popup],[class*=select],[class*=panel],[class*=overlay],[class*=Modal],[class*=modal],[role=menu],[role=listbox],[role=dialog]';
        document.querySelectorAll(sel).forEach(e => {
            const r = e.getBoundingClientRect();
            if (r.width < 60 || r.height < 30) return;
            if (r.width > 1000 || r.height > 900) return;
            const t = (e.innerText||'').trim();
            if (!t) return;
            res.push({cls:(e.className||'').toString().slice(0,90), text:t.slice(0,600),
                      x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
        });
        return res.slice(0, 15);
    }""")
    for g in got:
        log(f"  [{tag}] ({g['x']},{g['y']} {g['w']}x{g['h']}) [{g['cls']}] {g['text'][:400]!r}")
    return got


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE, headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1400, "height": 950},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)

    # 1) 点「深度思考」下拉
    for label in ["深度思考"]:
        try:
            loc = page.locator(f"button:has-text('{label}'), div:has-text('{label}')").last
            loc.click(timeout=5000)
            log(f"点击了 {label}")
        except Exception as e:
            log(f"点 {label} 失败: {str(e)[:80]}")
            continue
        time.sleep(2)
        page.screenshot(path=os.path.join(DUMP, "10_think_menu.png"))
        dump_popovers(page, "think")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(1)

    # 2) 找「更多」展开（模型选择可能藏在更多里）
    try:
        loc = page.locator("text=更多").last
        loc.click(timeout=5000)
        log("点击了 更多")
        time.sleep(2)
        page.screenshot(path=os.path.join(DUMP, "11_more_menu.png"))
        dump_popovers(page, "more")
        page.keyboard.press("Escape")
        time.sleep(1)
    except Exception as e:
        log("点更多失败:", str(e)[:80])

    # 3) 发一句「你好」进入会话后再找顶部模型 chip
    try:
        page.locator("[contenteditable]").first.click()
        page.keyboard.type("你好")
        page.keyboard.press("Enter")
        log("已发送 你好，等 12s")
        time.sleep(12)
        page.screenshot(path=os.path.join(DUMP, "12_in_chat.png"))
        try:
            page.pdf(path=os.path.join(DUMP, "12_in_chat.pdf"), format="A4", print_background=True)
        except Exception:
            pass
        # 抓整页所有短文本元素（模型chip一般很短）
        short = page.evaluate("""() => {
            const kws=['混元','Hy','DeepSeek','Pro','T1','V3','R1','快速','思考','模型','模式','专家','对话'];
            const res=[];
            document.querySelectorAll('*').forEach(e=>{
                if(e.children.length>1) return;
                const t=(e.innerText||'').trim();
                if(!t||t.length>24) return;
                if(!kws.some(k=>t.includes(k))) return;
                const r=e.getBoundingClientRect();
                if(r.width<5||r.height<5) return;
                res.push({tag:e.tagName,cls:(e.className||'').toString().slice(0,70),text:t,
                          x:Math.round(r.x),y:Math.round(r.y)});
            });
            return res.slice(0,60);
        }""")
        log("=== 会话中的模型/模式相关元素 ===")
        for s in short:
            log(f"  {s['tag']} [{s['cls']}] '{s['text']}' @({s['x']},{s['y']})")
        dump_popovers(page, "chat")
    except Exception as e:
        log("会话内探测失败:", str(e)[:100])

    page.screenshot(path=os.path.join(DUMP, "13_final.png"))
    ctx.close()

open(os.path.join(DUMP, "report2.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
