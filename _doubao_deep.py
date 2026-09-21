#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 1b: 深挖验证码 9 宫格图到底在哪（背景图/嵌套iframe/canvas），
并抓验证码下发网络响应看是否带图片语义/答案。不提交，只侦查。"""
import asyncio, json
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_deep.json"

HINTS = ["请选择所有", "符合", "并拖拽到", "拖拽到下方", "滑动验证", "拼图验证"]

async def find_cap_frame(page):
    for fr in page.frames:
        u = (fr.url or "")
        if "rmc.bytedance.com" in u or "verifycenter" in u:
            return fr
    return None

async def main():
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, headless=False, args=["--no-sandbox", "--disable-gpu"],
        viewport={"width": 1280, "height": 900},
        ignore_default_args=["--enable-automation"],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    # 抓验证码网络响应（在导航前挂好 listener）
    cap_payloads = []
    def on_response(resp):
        u = resp.url
        if ("verifycenter" in u or "captcha" in u or "verify" in u) and resp.status == 200:
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct or "js" in ct or "text" in ct:
                    body = resp.text()
                    if len(body) > 20 and ("image" in body.lower() or "answer" in body.lower()
                                           or "data" in body or "captcha" in body.lower()):
                        cap_payloads.append({"url": u[:140], "len": len(body), "snippet": body[:600]})
            except Exception:
                pass
    page.on("response", on_response)

    await page.goto("https://www.doubao.com/chat/", timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    cap = await find_cap_frame(page)
    if not cap:
        box = page.locator("div.tiptap.ProseMirror").first
        await box.click(); await page.wait_for_timeout(500)
        await page.keyboard.type("你好", delay=140); await page.wait_for_timeout(600)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await page.wait_for_timeout(20000)
        cap = await find_cap_frame(page)
    print("cap_frame:", (cap.url[:110] if cap else "NONE"), flush=True)

    data = {"cap_frame_url": (cap.url if cap else None), "all_frames": [f.url[:110] for f in page.frames]}
    if cap:
        # 1) 嵌套 frame 检查
        data["sub_frames_of_page"] = [(f.url[:110]) for f in page.frames]
        # 2) 背景图 / img / canvas 深挖（限在验证码容器里）
        data["cell_probe"] = await cap.evaluate("""() => {
            const res = {bg:[], img:[], canvas:[]};
            const box = document.querySelector('.vc-captcha-verify-img-prompt, [class*=img-prompt], .drag-area') || document.body;
            // 背景图
            box.querySelectorAll('*').forEach(el=>{
                const st = getComputedStyle(el);
                const bgi = st.backgroundImage;
                if (bgi && bgi !== 'none' && bgi.includes('url')) {
                    const r = el.getBoundingClientRect();
                    if (r.width>30 && r.height>30)
                        res.bg.push({x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),
                                     bgi:bgi.slice(0,150), cls:(el.className||'').toString().slice(0,40)});
                }
            });
            box.querySelectorAll('img').forEach(im=>{
                const r=im.getBoundingClientRect();
                if (r.width>20) res.img.push({x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),
                                              src:(im.src||'').slice(0,150), alt:im.alt||''});
            });
            box.querySelectorAll('canvas').forEach(cv=>{
                const r=cv.getBoundingClientRect();
                if (r.width>20) res.canvas.push({x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height)});
            });
            return res;
        }""")
        # 3) 若容器在 body 上没找到，扩大到整帧 body 找 9 个并排方块
        data["grid_guess"] = await cap.evaluate("""() => {
            const all=[...document.querySelectorAll('div,li,span')];
            const sq=all.filter(el=>{const r=el.getBoundingClientRect();
                return r.width>50&&r.width<160&&Math.abs(r.width-r.height)<30&&r.width>0;});
            return sq.slice(0,40).map(el=>({x:Math.round(el.getBoundingClientRect().left),y:Math.round(el.getBoundingClientRect().top),
                w:Math.round(el.getBoundingClientRect().width),cls:(el.className||'').toString().slice(0,40)}));
        }""")
    json.dump({"payloads": cap_payloads[:6], "frames": data, "grid": data.get("grid_guess")},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("payloads:", len(cap_payloads), flush=True)
    print(json.dumps(data, ensure_ascii=False)[:1500], flush=True)
    await ctx.close(); await pw.stop()

asyncio.run(main())
