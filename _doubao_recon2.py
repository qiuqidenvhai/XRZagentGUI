#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判清豆包当前状态：扫码登录墙 / 验证码墙 / 正常可用。
一次侦察，全部 dump 到 _doubao_state.json。"""
import asyncio, json, os, time
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
OUT = APP + r"\_doubao_state.json"
SHOT = APP + r"\_doubao_state.png"

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
    await page.wait_for_timeout(6000)

    state = {"url": page.url, "frames": []}
    for fr in page.frames:
        u = (fr.url or "")
        try:
            t = await fr.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            t = ""
        state["frames"].append({"url": u[:120], "len": len(t), "tail": t[-200:]})
        if "rmc.bytedance.com" in u or "verifycenter" in u:
            state["captcha_iframe"] = u[:160]
    # 主文档关键信号
    try:
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        body = ""
    state["body_len"] = len(body)
    state["body_head"] = body[:400]
    state["body_tail"] = body[-400:]
    signals = {
        "扫码登录": ("扫码登录" in body) or ("微信扫一扫" in body) or ("微信登录" in body and "未登录" in body),
        "登录墙关键字": [w for w in ("请登录","登录","扫码","微信登录","未登录") if w in body],
        "验证码关键字": [w for w in ("请选择所有","符合","拖拽到","滑动验证","完成拼图","安全验证","刷新","反馈") if w in body],
        "有对话气泡": ("回复" in body) or ("你好" in body),
        "能输入": any("tiptap" in f["url"] for f in state["frames"]) or True,
    }
    state["signals"] = signals
    await page.screenshot(path=SHOT, full_page=False)
    # 登录相关 cookie
    try:
        cks = await ctx.cookies("https://www.doubao.com")
        state["cookie_count"] = len(cks)
        state["cookie_names"] = sorted(set(c["name"] for c in cks))
        state["persistent_login_ckeys"] = [
            c["name"] for c in cks if c.get("expires", -1) > 0 and ("yuanbao" in c.get("domain","") or "doubao" in c.get("domain",""))
        ][:20]
    except Exception as e:
        state["cookie_err"] = str(e)[:80]
    json.dump(state, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("===SIGNALS===")
    print(json.dumps(signals, ensure_ascii=False, indent=1), flush=True)
    print("cookie_count:", state.get("cookie_count"), "names:", state.get("cookie_names"))
    print("shot:", SHOT)
    await ctx.close(); await pw.stop()

asyncio.run(main())
