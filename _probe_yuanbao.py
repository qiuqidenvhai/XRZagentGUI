# -*- coding: utf-8 -*-
"""摸清元宝网页上真实的「模型 / 模式」选项（后台必须已停，否则 profile 锁冲突）。"""
import sys, os, time, json
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


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE, headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1400, "height": 950},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)
    page.wait_for_timeout(3000)
    page.screenshot(path=os.path.join(DUMP, "01_loaded.png"))
    try:
        page.pdf(path=os.path.join(DUMP, "01_loaded.pdf"), format="A4", print_background=True)
    except Exception as e:
        log("pdf fail", e)

    # 1) 页面上所有看起来像「模型/模式」的可点元素
    cand = page.evaluate("""() => {
        const kws = ['混元','Hy','Hunyuan','DeepSeek','专家','对话','快速','思考','Pro','T1','V3','R1','模型','模式'];
        const res = [];
        document.querySelectorAll('*').forEach(e => {
            if (e.children.length > 2) return;
            const t = (e.innerText||'').trim();
            if (!t || t.length > 30) return;
            if (!kws.some(k => t.includes(k))) return;
            const r = e.getBoundingClientRect();
            if (r.width < 5 || r.height < 5) return;
            res.push({tag: e.tagName, cls: (e.className||'').toString().slice(0,80),
                      text: t, x: Math.round(r.x), y: Math.round(r.y),
                      w: Math.round(r.width), h: Math.round(r.height)});
        });
        return res.slice(0, 80);
    }""")
    log("=== 候选模型/模式元素 ===")
    for c in cand:
        log(f"  {c['tag']} [{c['cls']}] '{c['text']}' @({c['x']},{c['y']}) {c['w']}x{c['h']}")

    # 2) 逐个点一下顶部区域里最像模型切换的按钮，把弹出菜单的文字抓下来
    top = [c for c in cand if c["y"] < 260]
    log(f"\n=== 顶部候选 {len(top)} 个，逐个点击 ===")
    for c in top[:6]:
        try:
            el = page.locator(f"text={c['text']}").first
            el.click(timeout=4000)
        except Exception as e:
            log(f"  点 {c['text']!r} 失败: {str(e)[:60]}")
            continue
        page.wait_for_timeout(1500)
        menu = page.evaluate("""() => {
            const res = [];
            document.querySelectorAll('[class*=pop],[class*=menu],[class*=drop],[class*=popup],[class*=select],[class*=panel],[class*=Modal],[role=menu],[role=listbox]')
              .forEach(e => {
                const r = e.getBoundingClientRect();
                if (r.width < 80 || r.height < 40) return;
                if (r.width > 900 || r.height > 800) return;
                const t = (e.innerText||'').trim();
                if (!t || t.length > 500) return;
                res.push({cls: (e.className||'').toString().slice(0,70), text: t.slice(0,400)});
              });
            return res.slice(0, 10);
        }""")
        log(f"  点击 {c['text']!r} 后弹出的面板:")
        for m in menu:
            log(f"    [{m['cls']}] {m['text'][:220]!r}")
        page.screenshot(path=os.path.join(DUMP, f"menu_{abs(hash(c['text']))%10000}.png"))
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(800)

    # 3) 输入框附近的工具栏
    bar = page.evaluate("""() => {
        const res = [];
        document.querySelectorAll('[contenteditable]').forEach(ce => {
            const box = ce.closest('div');
            if (!box) return;
            let p = box.parentElement;
            for (let i=0;i<3 && p;i++,p=p.parentElement) {
                const t = (p.innerText||'').trim();
                if (t && t.length < 300) { res.push({lvl:i, text: t.slice(0,300)}); }
            }
        });
        return res.slice(0, 6);
    }""")
    log("\n=== 输入区工具栏文字 ===")
    for b in bar:
        log(f"  lvl{b['lvl']}: {b['text'][:220]!r}")

    page.screenshot(path=os.path.join(DUMP, "02_final.png"))
    try:
        page.pdf(path=os.path.join(DUMP, "02_final.pdf"), format="A4", print_background=True)
    except Exception as e:
        log("pdf fail", e)
    ctx.close()

open(os.path.join(DUMP, "report.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done ->", DUMP)
