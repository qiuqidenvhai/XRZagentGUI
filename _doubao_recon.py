#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""侦察豆包 profile 当前页面状态：验证码长什么样、在不在 iframe 里。
不发消息、不操作，只截图 + dump 结构，输出 JSON 摘要。"""
import asyncio, json, time
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_recon.json"
SHOT = APP + r"\_doubao_recon.png"

CAPTCHA_HINTS = [
    "请选择所有符合", "符合上述描述", "符合上图", "并拖拽到", "拖拽到下方",
    "拖拽到这里", "按住滑块", "向右滑动", "请完成验证", "滑动验证",
    "点击验证", "完成拼图", "拼图验证", "安全验证", "验证一下",
    "在厨房", "属于动物", "物品", "图片",
]

async def main():
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE,
        headless=False,
        args=["--no-sandbox", "--disable-gpu"],
        viewport={"width": 1280, "height": 900},
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print("现有页面:", [p.url for p in ctx.pages], flush=True)
    try:
        await page.goto("https://www.doubao.com/chat/", timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        print("goto 异常:", e, flush=True)
    await page.wait_for_timeout(6000)
    await page.screenshot(path=SHOT, full_page=False)
    print("截图 ->", SHOT, flush=True)

    info = {"url": page.url, "title": await page.title()}
    # 主文档 + 所有 frame 的文本（找验证码特征）
    frames = [page] + list(page.frames[1:])
    frame_report = []
    for i, fr in enumerate(frames):
        try:
            t = await fr.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:
            t = ""
        is_main = (fr is page)
        fname = "main" if is_main else fr.name
        furl = (page.url if is_main else fr.url)[:100]
        hits = [h for h in CAPTCHA_HINTS if h in t]
        frame_report.append({
            "index": i, "name": fname, "url": furl,
            "len": len(t), "hint_hits": hits, "preview": t[:400],
        })
    info["frames"] = frame_report
    # 找图片类可交互元素（图片验证码的候选图块 / 滑条 / 提交按钮）
    try:
        elems = await page.evaluate("""() => {
            const out = [];
            const scan = (doc, label) => {
                const imgs = doc.querySelectorAll('img');
                imgs.forEach(im => {
                    const r = im.getBoundingClientRect();
                    if (r.width > 30 && r.height > 30 && (im.src||'').length > 0) {
                        out.push({label, tag:'img', w:Math.round(r.width), h:Math.round(r.height),
                                  src:(im.src||'').slice(0,120), alt:im.alt||''});
                    }
                });
                const btns = doc.querySelectorAll('button, [role=button]');
                btns.forEach(b => {
                    const r = b.getBoundingClientRect();
                    const txt = (b.innerText||b.textContent||'').trim().slice(0,30);
                    if (r.width > 0 && txt) out.push({label, tag:'button', txt});
                });
                const divs = doc.querySelectorAll('div[class*=drag], div[class*=slider], div[class*=captcha], div[class*=verify], div[class*=slide]');
                divs.forEach(d => {
                    const r = d.getBoundingClientRect();
                    out.push({label, tag:'div-captcha', cls:(d.className||'').toString().slice(0,80),
                              w:Math.round(r.width), h:Math.round(r.height)});
                });
            };
            scan(document, 'main');
            for (const f of document.querySelectorAll('iframe')) {
                try { scan(f.contentDocument, 'iframe:'+ (f.src||'').slice(0,40)); } catch(e) { out.push({label:'iframe-x', tag:'iframe-x', url:(f.src||'').slice(0,80)}); }
            }
            return out;
        }""")
        info["elements"] = elems[:60]
    except Exception as e:
        info["elements_error"] = str(e)
    json.dump(info, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("摘要 ->", OUT, flush=True)
    print("url:", info["url"], flush=True)
    for fr in frame_report:
        if fr["hint_hits"]:
            print("frame", fr["index"], fr["name"], "命中:", fr["hint_hits"][:8], flush=True)
    await ctx.close()
    await pw.stop()

asyncio.run(main())
