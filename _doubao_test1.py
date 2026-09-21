#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""单条慢速验证：连豆包 live profile，发一条「1+1等于几」，读回复，判断登录/风控状态。
绝不 loop、不砸。发完等 25s 就收。"""
import asyncio, json, os
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_test1.json"

async def main():
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, headless=False, args=["--no-sandbox", "--disable-gpu"],
        viewport={"width": 1280, "height": 900}, ignore_default_args=["--enable-automation"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    print("pages:", [p.url for p in ctx.pages], flush=True)
    try:
        await page.goto("https://www.doubao.com/chat/", timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        print("goto err:", str(e)[:80], flush=True)
    await page.wait_for_timeout(5000)

    # 是否已有验证码 iframe（进页面就弹）
    cap_iframe = any("rmc.bytedance.com" in (f.url or "") or "verifycenter" in (f.url or "") for f in page.frames)
    print("captcha_iframe_on_load:", cap_iframe, flush=True)

    res = {"captcha_on_load": cap_iframe}
    if not cap_iframe:
        box = page.locator("div.tiptap.ProseMirror").first
        await asyncio.wait_for(box.click(), 12)
        await page.wait_for_timeout(500)
        await page.keyboard.type("1+1等于几", delay=150)
        await page.wait_for_timeout(500)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        print("sent, waiting 25s...", flush=True)
        await page.wait_for_timeout(25000)
        cap_iframe = any("rmc.bytedance.com" in (f.url or "") or "verifycenter" in (f.url or "") for f in page.frames)
        res["captcha_after"] = cap_iframe
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        res["body_tail"] = body[-500:]
        # AI 回复通常在 md-box-root
        try:
            replies = await page.evaluate("""() => {
                const out=[];
                document.querySelectorAll('[class*="md-box-root"],[class*="prose"],[class*="markdown"]').forEach(el=>{
                    const t=(el.innerText||'').trim();
                    if(t) out.push(t.slice(0,300));
                });
                return out;
            }""")
            res["reply_candidates"] = replies[:6]
        except Exception as e:
            res["reply_err"] = str(e)[:60]
    else:
        res["skipped_send_captcha_present"] = True
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        res["body_tail"] = body[-500:]
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=== RESULT ===")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:1500], flush=True)
    await ctx.close(); await pw.stop()

asyncio.run(main())
