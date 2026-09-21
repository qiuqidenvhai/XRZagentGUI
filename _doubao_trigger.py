#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""慢速发一条消息给豆包，触发/检测反自动化验证墙，dump 结构 + 截图。
慢速模拟真人（逐字输入 90ms、CDP 受信 Enter），尽量不激怒风控。"""
import asyncio, json, time
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_trig.json"
SHOT = APP + r"\_doubao_trig.png"
CAP = r"D:\软件\XianRenZhangAgent\_doubao_cap.png"

HINTS = ["请选择所有符合", "符合上述描述", "符合上图", "并拖拽到", "拖拽到下方",
         "拖拽到这里", "按住滑块", "向右滑动", "请完成验证", "滑动验证",
         "点击验证", "完成拼图", "拼图验证", "安全验证", "验证一下", "刷新"]

async def main():
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, headless=False,
        args=["--no-sandbox", "--disable-gpu"],
        viewport={"width": 1280, "height": 900},
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    print("pages:", [p.url for p in ctx.pages], flush=True)
    await page.goto("https://www.doubao.com/chat/", timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(6000)

    # 慢速输入一条平常消息（模拟真人节奏）
    box = page.locator("div.tiptap.ProseMirror").first
    await box.click()
    await page.wait_for_timeout(800)
    await page.keyboard.type("你好", delay=120)
    await page.wait_for_timeout(700)
    # CDP 受信 Enter 发送
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
    print("已发送「你好」，等待 25s 观察验证墙/回复...", flush=True)
    await page.wait_for_timeout(25000)

    await page.screenshot(path=SHOT, full_page=False)
    # dump 所有 frame + 候选图块
    frames = [page] + [f for f in page.frames if f is not page]
    fr_txt = []
    cap_found = False
    for fr in frames:
        try:
            t = await fr.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:
            t = ""
        if any(h in t for h in HINTS):
            cap_found = True
        fr_txt.append({"name": fr.name if fr is not page else "main", "url": (fr.url or "")[:120], "len": len(t), "cap": any(h in t for h in HINTS)})
    # 可交互元素：img 块 / 按钮 / 验证容器（主文档+可访问 iframe）
    elems = []
    for fr in frames:
        try:
            el = await fr.evaluate("""() => {
                const out = [];
                document.querySelectorAll('img').forEach(im => {
                    const r = im.getBoundingClientRect();
                    if (r.width > 60 && r.height > 60 && (im.src||'').length > 0 && im.getBoundingClientRect().top > 100)
                        out.push({tag:'img', x:Math.round(r.left), y:Math.round(r.top), w:Math.round(r.width), h:Math.round(r.height), src:(im.src||'').slice(0,100), alt:im.alt||''});
                });
                document.querySelectorAll('button,[role=button]').forEach(b => {
                    const t = (b.innerText||'').trim().slice(0,24);
                    const r = b.getBoundingClientRect();
                    if (t && r.width>0 && (t.includes('确认')||t.includes('提交')||t.includes('刷新')||t.includes('滑块')||t.includes('拖动')))
                        out.push({tag:'btn', x:Math.round(r.left), y:Math.round(r.top), w:Math.round(r.width), h:Math.round(r.height), txt:t});
                });
                document.querySelectorAll('[class*=captcha],[class*=verify],[class*=drag],[class*=slider],[class*=slide]').forEach(d => {
                    const r = d.getBoundingClientRect();
                    if (r.width>20) out.push({tag:'capdiv', cls:(d.className||'').toString().slice(0,60), x:Math.round(r.left), y:Math.round(r.top), w:Math.round(r.width), h:Math.round(r.height)});
                });
                return out;
            }""")
            elems.extend(el)
        except Exception:
            pass

    # 若命中验证墙：把验证区域（图块所在区域）单独截一张
    cap_shot = None
    if cap_found and elems:
        xs = [e['x'] for e in elems if 'x' in e]; ys = [e['y'] for e in elems if 'y' in e]
        if xs:
            x0 = max(0, min(xs) - 20); y0 = max(0, min(ys) - 60)
            x1 = min(1280, max(e['x'] + e.get('w', 0) for e in elems if 'x' in e) + 20)
            y1 = min(900, max(e['y'] + e.get('h', 0) for e in elems if 'y' in e) + 90)
            await page.screenshot(path=CAP, clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
            cap_shot = CAP

    report = {"url": page.url, "captcha_detected": cap_found, "frames": fr_txt, "elements": elems[:80], "full_shot": SHOT, "cap_shot": cap_shot}
    json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("captcha_detected =", cap_found, flush=True)
    print("frames:", json.dumps(fr_txt, ensure_ascii=False)[:300], flush=True)
    print("elem_count:", len(elems), "cap_shot:", cap_shot, flush=True)
    # 先不关浏览器，留在原地等我下一步（子进程里保持 3 秒后关，因为后续要用独立脚本操作）
    await ctx.close()
    await pw.stop()

asyncio.run(main())
