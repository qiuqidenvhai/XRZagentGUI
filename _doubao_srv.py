#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""豆包语义验证码 文件驱动求解器。
- 启动：连豆包 live profile，若进页面就有验证码则直接用；没有就慢速发一条消息触发。
- 触发后：把提示词(_cap_prompt.txt) + 9 个宫格(_cap_cell_0..8.png) + 提交坐标(_cap_state.json) dump 出来。
- 然后挂住，轮询 _cap_cmd.json（由主控/另一进程写入）：
    {"idx":[0,3,5]}  -> 真人节奏点选这些宫格 + 点提交，等 15s，判定是否消失；
                         消失写 _cap_done.json；没消失则重新 dump 新一组并写 _cap_result.json
    {"cmd":"refresh"}-> 点刷新重出题，重新 dump
    {"cmd":"quit"}   -> 关闭
- 每 1s 写 _cap_heartbeat.txt，卡死立刻可见。"""
import asyncio, json, os, time, traceback
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
CMD  = APP + r"\_cap_cmd.json"
STATE = APP + r"\_cap_state.json"
PROMPT = APP + r"\_cap_prompt.txt"
RESULT = APP + r"\_cap_result.json"
DONE  = APP + r"\_cap_done.json"
HB    = APP + r"\_cap_heartbeat.txt"
AFTER = APP + r"\_cap_after.png"
CELLS = [os.path.join(APP, f"_cap_cell_{i}.png") for i in range(9)]

def hb(msg):
    try:
        with open(HB, "w", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}  {msg}\n")
    except Exception:
        pass

def read_cmd():
    if not os.path.exists(CMD):
        return None
    try:
        c = json.load(open(CMD, encoding="utf-8"))
        os.remove(CMD)
        return c
    except Exception:
        return None

def find_cap(page):
    for fr in page.frames:
        u = (fr.url or "")
        if "rmc.bytedance.com" in u or "verifycenter" in u:
            return fr
    return None

async def _ev(frame, expr, timeout=15):
    try:
        return await asyncio.wait_for(frame.evaluate(expr), timeout)
    except Exception as e:
        hb("  _ev err: " + str(e)[:80])
        return None

async def dump(page, label=""):
    cap = find_cap(page)
    if not cap:
        return False
    state = {"label": label}
    # 提示词
    t = await _ev(cap, "() => document.body ? document.body.innerText : ''")
    state["prompt"] = (t or "")[:400]
    open(PROMPT, "w", encoding="utf-8").write(state["prompt"])
    # iframe 偏移（主文档空间）
    boxes = await _ev(page, """() => {
        const fs=[...document.querySelectorAll('iframe')];
        const f=fs.find(x=>(x.src||'').includes('rmc.bytedance.com')||(x.src||'').includes('verifycenter'));
        if(f){const r=f.getBoundingClientRect(); return {x:r.left,y:r.top};}
        return {x:0,y:0};
    }""")
    boxes = boxes or {"x": 0, "y": 0}
    state["offset"] = boxes
    # 9 宫格（frame 内 .canvas-container）
    inner = await _ev(cap, """() => {
        const out=[];
        document.querySelectorAll('.canvas-container').forEach(el=>{
            const r=el.getBoundingClientRect();
            if(r.width>50) out.push({x:r.left,y:r.top,w:r.width,h:r.height});
        });
        out.sort((a,b)=> (a.y-b.y) || (a.x-b.x));
        return out.slice(0,9);
    }""") or []
    cells = []
    for b in inner:
        cells.append({"center": [int(b["x"]+b["w"]/2)+int(boxes["x"]),
                                 int(b["y"]+b["h"]/2)+int(boxes["y"])],
                      "box": b})
    state["cells"] = cells
    # 逐格截图（主文档坐标裁剪，越界容错）
    nshots = 0
    for i, c in enumerate(cells):
        cx, cy = c["center"]; cw, ch = c["box"]["w"], c["box"]["h"]
        x0 = max(0, int(cx-cw/2)); y0 = max(0, int(cy-ch/2))
        w = min(cw, 1280-x0); h = min(ch, 900-y0)
        if w <= 0 or h <= 0:
            continue
        try:
            await asyncio.wait_for(page.screenshot(path=CELLS[i],
                                                   clip={"x":x0,"y":y0,"width":w,"height":h}), 15)
            nshots += 1
        except Exception as e:
            hb(f"  shot{i} err {str(e)[:50]}")
    state["shots"] = nshots
    # 提交按钮 + 拖拽区 + 关闭按钮
    sb = await _ev(cap, """() => {
        const el=document.querySelector('.vc-captcha-verify-pb')||document.querySelector('[class*=verify-pb]');
        if(!el) return null; const r=el.getBoundingClientRect();
        return {cx:r.left+r.width/2, cy:r.top+r.height/2,
                disabled: !!(el.className||'').includes('disable')};
    }""")
    state["submit"] = {"cx": int(sb["cx"])+int(boxes["x"]), "cy": int(sb["cy"])+int(boxes["y"]),
                       "disabled": sb.get("disabled", False)} if sb else None
    da = await _ev(cap, """() => {
        const el=document.querySelector('.drag-area')||document.querySelector('[class*=drag-area]');
        if(!el) return null; const r=el.getBoundingClientRect();
        return {cx:r.left+r.width/2, cy:r.top+r.height/2, w:r.width, h:r.height};
    }""")
    state["drag_area"] = {"cx": int(da["cx"])+int(boxes["x"]), "cy": int(da["cy"])+int(boxes["y"]),
                           "w": int(da["w"]), "h": int(da["h"])} if da else None
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    hb(f"DUMP {label} cells={len(cells)} shots={nshots} submit={state['submit']} prompt={state['prompt'][:30]!r}")
    return len(cells) > 0

async def trigger(page):
    """慢速发一条消息触发验证码。多重选择器 + 坐标兜底 + 全程兜底，不 crash。"""
    send_ok = False
    composer_selectors = [
        "div.tiptap.ProseMirror", ".tiptap", '[contenteditable="true"]',
        "div.ql-editor", "textarea.prose-mirror", "div[contenteditable]",
    ]
    target = None
    for sel in composer_selectors:
        try:
            await page.wait_for_selector(sel, timeout=4000, state="attached")
            target = page.locator(sel).last
            await asyncio.wait_for(target.click(), 8)
            send_ok = True
            hb(f"trigger: clicked {sel}")
            break
        except Exception:
            continue
    if not send_ok:
        # 坐标兜底：豆包输入框在页面底部中央
        try:
            await page.mouse.click(640, 800)
            await page.wait_for_timeout(300)
            send_ok = True
            hb("trigger: coordinate fallback (640,800)")
        except Exception as e:
            hb("trigger: fallback err " + str(e)[:50])
    if not send_ok:
        return False
    await page.wait_for_timeout(500)
    try:
        await page.keyboard.type("你好", delay=150)
        hb("trigger: typed 你好")
    except Exception as e:
        hb("trigger: type err " + str(e)[:60])
    await page.wait_for_timeout(400)
    try:
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Input.dispatchKeyEvent", {"type":"keyDown","key":"Enter","code":"Enter","windowsVirtualKeyCode":13,"nativeVirtualKeyCode":13})
        await cdp.send("Input.dispatchKeyEvent", {"type":"keyUp","key":"Enter","code":"Enter","windowsVirtualKeyCode":13,"nativeVirtualKeyCode":13})
        hb("trigger: Enter sent")
        return True
    except Exception as e:
        # Enter 失败就退化成按键盘 Enter
        try:
            await page.keyboard.press("Enter")
            hb("trigger: keyboard Enter fallback")
            return True
        except Exception as e2:
            hb("trigger: enter err " + str(e2)[:50])
            return False

async def main():
    for f in (CMD, RESULT, DONE):
        if os.path.exists(f):
            try: os.remove(f)
            except Exception: pass
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE, headless=False, args=["--no-sandbox","--disable-gpu"],
        viewport={"width":1280,"height":900}, ignore_default_args=["--enable-automation"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    hb("pages: " + str([p.url for p in ctx.pages]))
    try:
        await page.goto("https://www.doubao.com/chat/", timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        hb("goto err " + str(e)[:80])
    await page.wait_for_timeout(5000)
    hb("loaded")

    cap = find_cap(page)
    if not cap:
        hb("no captcha on load -> trigger")
        await trigger(page)
        for i in range(20):
            await page.wait_for_timeout(1500)
            cap = find_cap(page)
            if cap:
                hb(f"captcha frame at poll {i}")
                break
    else:
        hb("captcha present on load")

    if not cap:
        # 没弹验证码 = 账号没被风控，直接就通了
        json.dump({"ok": True, "reason": "no_captcha_after_trigger"}, open(DONE, "w", encoding="utf-8"))
        hb("NO CAPTCHA - account clear, done")
        await ctx.close(); await pw.stop()
        return

    ok = await dump(page, "initial")
    if not ok:
        hb("dump failed - retrying trigger")
        await trigger(page)
        await page.wait_for_timeout(8000)
        ok = await dump(page, "retry")
    hb("READY - waiting for _cap_cmd.json")

    # 主控循环
    last_state_write = time.time()
    while True:
        cmd = read_cmd()
        now = time.time()
        # 若验证码自己超时消失（超过 90s 没新题），自动重新 dump 一组新的
        cap_now = find_cap(page)
        if cmd:
            if "idx" in cmd:
                st = json.load(open(STATE, encoding="utf-8"))
                capf = find_cap(page)
                if capf is None:
                    hb("solve: captcha frame gone, redump")
                    ok = await dump(page, "reappear")
                    capf = find_cap(page)
                if capf is None:
                    hb("solve: captcha still gone after redump -> write no-op result")
                    json.dump({"idx": cmd["idx"], "success": False, "captcha_still": False,
                               "note": "frame gone"}, open(RESULT, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    continue
                hb(f"solve idx={cmd['idx']} via frame-locators")
                sel_cells = capf.locator(".canvas-container")
                ncell = await sel_cells.count()
                hb(f"  frame .canvas-container count={ncell}")
                # 1) 逐个选中（frame 内定位器点击，事件正确路由进跨域 iframe）
                clicked = 0
                for i in cmd["idx"]:
                    if i >= ncell:
                        continue
                    try:
                        await sel_cells.nth(i).click(timeout=6000)
                        clicked += 1
                        await page.wait_for_timeout(450)
                    except Exception as e:
                        hb(f"  select{i} err {str(e)[:40]}")
                hb(f"selected {clicked}/{len(cmd['idx'])}")
                # 2) 选中后诊断提交按钮状态
                sub_loc = capf.locator(".vc-captcha-verify-pb, [class*=verify-pb]").first
                submit_enabled = False
                try:
                    cls = (await sub_loc.get_attribute("class")) or ""
                    submit_enabled = "disable" not in cls
                    hb(f"submit class={cls!r} enabled={submit_enabled}")
                except Exception as e:
                    hb("submit state err " + str(e)[:40])
                # 3) 拖拽兜底：把第一张选中格拖到 drag_area（题目要求"拖拽到下方"）
                dragged = False
                try:
                    da = capf.locator(".drag-area, [class*=drag-area]").first
                    if await da.count() > 0 and cmd["idx"]:
                        first = cmd["idx"][0]
                        await sel_cells.nth(first).drag_to(da, timeout=8000)
                        dragged = True
                        hb("drag first->drag_area done")
                        await page.wait_for_timeout(800)
                except Exception as e:
                    hb("drag err " + str(e)[:40])
                # 4) 点提交（若已 enabled）
                submitted = False
                if submit_enabled or dragged:
                    try:
                        await sub_loc.click(timeout=6000)
                        submitted = True
                        hb("submit clicked")
                    except Exception as e:
                        hb("submit click err " + str(e)[:40])
                hb(f"actions: clicked={clicked} dragged={dragged} submitted={submitted} waiting 15s")
                await page.wait_for_timeout(15000)
                still = find_cap(page) is not None
                tail = ""
                try:
                    tail = (await _ev(page, "() => document.body ? document.body.innerText : ''") or "")[-400:]
                except Exception:
                    pass
                if still:
                    await page.screenshot(path=AFTER, full_page=False)
                    json.dump({"idx": cmd["idx"], "success": False, "captcha_still": True,
                               "body_tail": tail, "after_shot": AFTER},
                              open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    # 重出一组
                    ok = await dump(page, "after_fail")
                    hb(f"FAILED (captcha still up), redumped cells={ok}")
                else:
                    json.dump({"idx": cmd["idx"], "success": True, "captcha_gone": True,
                               "body_tail": tail}, open(DONE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    hb("SUCCESS - captcha gone, done written")
                    break
            elif cmd.get("cmd") == "refresh":
                capf = find_cap(page)
                if capf:
                    await _ev(capf, "() => { const el=document.querySelector('.vc-captcha-refresh'); if(el) el.click(); }")
                await page.wait_for_timeout(3500)
                ok = await dump(page, "refresh")
                hb(f"refreshed, cells={ok}")
            elif cmd.get("cmd") == "quit":
                hb("quit requested")
                break
        else:
            # 无指令：若验证码已消失（风控解锁/超时自然过期），主动重出一组，避免 stale 坐标
            if cap_now is None and (now - last_state_write > 20):
                hb("captcha auto-expired, retrigger")
                await trigger(page)
                for i in range(12):
                    await page.wait_for_timeout(1500)
                    if find_cap(page): break
                ok = await dump(page, "auto")
                last_state_write = time.time()
                hb(f"auto redump cells={ok}")
            elif cap_now is not None:
                last_state_write = now
        await page.wait_for_timeout(1000)

    hb("closing")
    await ctx.close(); await pw.stop()

if __name__ == "__main__":
    asyncio.run(main())
