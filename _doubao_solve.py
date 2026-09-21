#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重新挂到豆包 live profile，检测/触发当前验证码墙，dump 出可解信息。
Phase 1: 只 dump（提示词 + 9 宫格图 src + 提交按钮 + 截图），决定 Phase 2 怎么解。
慢速真人节奏，单次触发，绝不高频。"""
import asyncio, json, time, sys
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_cap.json"
SHOT = APP + r"\_doubao_cap_full.png"
CAP = APP + r"\_doubao_cap.png"

HINTS = ["请选择所有", "符合上述描述", "符合上图", "并拖拽到", "拖拽到下方",
         "拖拽到这里", "按住滑块", "向右滑动", "请完成验证", "滑动验证",
         "点击验证", "完成拼图", "拼图验证", "安全验证", "验证一下"]

PHASE = sys.argv[1] if len(sys.argv) > 1 else "dump"

async def find_captcha_frame(page):
    """返回 (frame, iframe_url_hint)。验证码在 rmc.bytedance.com 的 iframe 里。"""
    for fr in page.frames:
        u = (fr.url or "")
        if "rmc.bytedance.com" in u or "verifycenter" in u:
            return fr
    return None

async def dump_captcha(page):
    fr = await find_captcha_frame(page)
    if not fr:
        return {"found": False}
    data = {"found": True, "frame_url": fr.url[:140]}
    # 提示词（标题）
    try:
        data["prompt"] = await fr.evaluate(
            "() => { const t=document.querySelector('.tit,[class*=bar--title],[class*=title]'); return t? t.innerText : ''; }")
    except Exception as e:
        data["prompt"] = "ERR " + str(e)
    # 全量 body 文本（兜底读提示）
    try:
        data["body_text"] = (await fr.evaluate("() => document.body? document.body.innerText : ''"))[:400]
    except Exception:
        data["body_text"] = ""
    # 9 宫格图
    try:
        imgs = await fr.evaluate("""() => {
            const out=[];
            document.querySelectorAll('img').forEach(im=>{
                const r=im.getBoundingClientRect();
                if(r.width>40 && r.height>40)
                    out.push({x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),
                              src:(im.src||'').slice(0,160), alt:im.alt||'',
                              data:JSON.stringify(Object.assign({}, im.dataset)).slice(0,160)});
            });
            return out;
        }""")
        data["imgs"] = imgs
    except Exception as e:
        data["imgs"] = "ERR " + str(e)
    # 拖拽区 / 提交按钮 / 刷新
    try:
        acts = await fr.evaluate("""() => {
            const out=[];
            ['drag-area','vc-captcha-verify-pb','vc-captcha-refresh','vc-captcha-feedback','vc-captcha-verify-action','vc-captcha-close-btn']
            .forEach(sel=>{
                document.querySelectorAll('[class*="'+sel+'"]').forEach(el=>{
                    const r=el.getBoundingClientRect();
                    if(r.width>0) out.push({sel:sel, cls:(el.className||'').toString().slice(0,50),
                                             x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),
                                             txt:(el.innerText||'').trim().slice(0,20), tag:el.tagName});
                });
            });
            return out;
        }""")
        data["actions"] = acts
    except Exception as e:
        data["actions"] = "ERR " + str(e)
    # 容器位置（iframe 相对主文档）
    try:
        data["iframe_box"] = await page.evaluate("""() => {
            const fs=[...document.querySelectorAll('iframe')];
            return fs.map(f=>{const r=f.getBoundingClientRect();
                return {src:(f.src||'').slice(0,90), x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height)};});
        }""")
    except Exception:
        data["iframe_box"] = []
    # 截图
    await page.screenshot(path=SHOT, full_page=False)
    try:
        fb = data["iframe_box"]
        for b in fb:
            if "rmc" in (b.get("src") or "") or b["w"] > 200:
                await page.screenshot(path=CAP, clip={"x": max(0,b["x"]), "y": max(0,b["y"]),
                                                       "width": min(1280, b["w"]), "height": min(900, b["h"])})
                break
    except Exception:
        pass
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("prompt:", data.get("prompt"), flush=True)
    print("body_text:", (data.get("body_text") or "")[:200], flush=True)
    print("imgs:", len(data.get("imgs", [])), flush=True)
    for im in data.get("imgs", [])[:12]:
        print("  img", im.get("w"), "x", im.get("h"), "alt=", repr(im.get("alt")), "src=", im.get("src", "")[:60], flush=True)
    print("actions:", json.dumps(data.get("actions", []), ensure_ascii=False)[:400], flush=True)

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
    await page.goto("https://www.doubao.com/chat/", timeout=40000, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    # 先看是否已有验证码墙（服务端已锁定，进页面就弹）
    cap_frame = await find_captcha_frame(page)
    if not cap_frame:
        # 没有墙 → 慢速发一条平常消息触发（真人节奏，单次）
        print("no captcha on load -> slowly type 你好 to trigger", flush=True)
        box = page.locator("div.tiptap.ProseMirror").first
        await box.click()
        await page.wait_for_timeout(600)
        await page.keyboard.type("你好", delay=130)
        await page.wait_for_timeout(700)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        print("sent 你好, waiting 22s", flush=True)
        await page.wait_for_timeout(22000)
        cap_frame = await find_captcha_frame(page)

    print("captcha_frame:", (cap_frame.url[:120] if cap_frame else "NONE"), flush=True)
    if cap_frame:
        await dump_captcha(page)
    else:
        # 没触发 → 可能已解锁 / 正常回复了，dump 主文档判断
        txt = await page.evaluate("() => document.body? document.body.innerText : ''")
        ok = ("回复" in txt or "你好" in txt) and not any(h in txt for h in HINTS)
        print("NO_CAPTCHA. clean_reply_guess=", ok, flush=True)
        print("body_tail:", txt[-300:], flush=True)
        json.dump({"found": False, "body_tail": txt[-300:], "clean_reply_guess": ok},
                  open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    await ctx.close()
    await pw.stop()

asyncio.run(main())
