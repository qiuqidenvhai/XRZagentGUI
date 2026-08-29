# 千问消息 DOM 结构诊断：找出真正代表「AI 一条消息」的容器
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = r"D:\软件\XianRenZhangAgent\xrz_data\.xianrenzhang_agent\browser_profiles\tongyi"

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE, headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        for _ in range(4):
            try:
                await page.goto("https://chat.qwen.ai/", timeout=45000); break
            except Exception:
                await asyncio.sleep(2)
        await asyncio.sleep(5)

        # 发一条消息
        inp = page.locator("textarea").first
        await inp.click()
        await inp.fill("请只回复四个字：确认收到")
        await asyncio.sleep(0.5)
        cdp = await ctx.new_cdp_session(page)
        for evt in ("keyDown", "keyUp"):
            await cdp.send("Input.dispatchKeyEvent", {
                "type": evt, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
        await asyncio.sleep(8)  # 等千问生成完毕

        # 诊断：找出所有 [class*='message'] 元素，打印它们的 class/text/深度
        dump = await page.evaluate("""() => {
            const out = [];
            const all = [...document.querySelectorAll('[class*="message"]')];
            for (const el of all) {
                if (!el.offsetParent) continue;  // 不可见的跳过
                const cls = String(el.className).slice(0, 120);
                const text = (el.innerText || '').replace(/\s+/g, ' ').slice(0, 80);
                const depth = (() => { let d=0, p=el; while(p){d++;p=p.parentElement;} return d; })();
                out.push({tag: el.tagName, cls, depth, text});
            }
            // 按深度排序（浅层的可能是外层包裹容器）
            out.sort((a,b) => a.depth - b.depth);
            return out;
        }""")
        print("=== [class*='message'] 所有可见元素 ===")
        for d in dump:
            print(f"  {d['tag']:8} depth={d['depth']} cls={d['cls'][:90]:90} text={d['text'][:60]}")

        # 也看 data-testid
        dump2 = await page.evaluate("""() => {
            const out = [];
            const all = [...document.querySelectorAll('[data-testid]')];
            for (const el of all) {
                if (!el.offsetParent) continue;
                out.push({
                    tag: el.tagName,
                    testid: el.getAttribute('data-testid'),
                    cls: String(el.className).slice(0, 100),
                    text: (el.innerText||'').replace(/\s+/g,' ').slice(0, 60),
                });
            }
            return out;
        }""")
        print("\n=== 带 data-testid 的可见元素 ===")
        for d in dump2:
            print(f"  {d['tag']:8} testid={d['testid']!r:20} cls={d['cls'][:70]:70} text={d['text'][:50]}")

        await ctx.close()

asyncio.run(main())
