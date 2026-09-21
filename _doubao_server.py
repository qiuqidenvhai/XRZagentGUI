#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""豆包验证码 现场求解服务 v2。
- HTTP 控制通道先起（9555），浏览器步骤做成带超时的后台任务，绝不卡死控制面。
- 9 个宫格是 canvas，各自截图成 _cap_cell_0..8.png；提示词写 _cap_prompt.txt；
  坐标/偏移写 _cap_cells.json。
- 主控（另一个会话）读图决策后：
    POST /solve {"idx":[0,3,...]}  -> 真人节奏点选 + 点提交，等 15s 报告是否消失
    POST /refresh                  -> 重新出题并重新 dump
    POST /quit                     -> 关闭
"""
import asyncio, json, os, threading, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.async_api import async_playwright

APP = r"D:\软件\XianRenZhangAgent"
PROFILE = APP + r"\xrz_data\.xianrenzhang_agent\browser_profiles\doubao"
CELLS = [os.path.join(APP, f"_cap_cell_{i}.png") for i in range(9)]
PROMPT = os.path.join(APP, "_cap_prompt.txt")
COORDS = os.path.join(APP, "_cap_cells.json")
AFTER = os.path.join(APP, "_cap_after.png")
PORT = 9555

state = {"phase": "init", "msg": "", "cells": 0, "prompt": "", "waiting": False, "ready": False}

def log(m):
    print(m, flush=True)
    state["msg"] = m

server_ctx = {"loop": None, "ctx": None, "page": None, "quit": False}

async def find_cap(page):
    for fr in page.frames:
        u = (fr.url or "")
        if "rmc.bytedance.com" in u or "verifycenter" in u:
            return fr
    return None

async def _ev(frame, expr, timeout=15):
    """带超时的 frame.evaluate，避免 stale/跨源 frame 卡死。"""
    try:
        return await asyncio.wait_for(frame.evaluate(expr), timeout)
    except Exception as e:
        log(f"  _ev err: {str(e)[:60]}")
        return None

async def dump_cells(page):
    cap = await find_cap(page)
    if not cap:
        log("dump: no captcha frame")
        return False
    # 提示词
    try:
        t = await _ev(cap, "() => document.body ? document.body.innerText : ''")
        state["prompt"] = (t or "")[:400]
        open(PROMPT, "w", encoding="utf-8").write(state["prompt"])
    except Exception as e:
        log("dump prompt err: " + str(e)); state["prompt"] = ""
    log("dump: got prompt " + repr(state["prompt"][:40]))
    # iframe 偏移（主文档空间）
    try:
        boxes = await _ev(page, """() => {
            const fs=[...document.querySelectorAll('iframe')];
            const f=fs.find(x=>(x.src||'').includes('rmc.bytedance.com')||(x.src||'').includes('verifycenter'));
            if(f){const r=f.getBoundingClientRect(); return {x:r.left,y:r.top};}
            return {x:0,y:0};
        }""")
        boxes = boxes or {"x": 0, "y": 0}
    except Exception as e:
        log("dump boxes err " + str(e)); boxes = {"x": 0, "y": 0}
    # 9 宫格（frame 内）
    try:
        inner = await _ev(cap, """() => {
            const out=[];
            document.querySelectorAll('.canvas-container').forEach(el=>{
                const r=el.getBoundingClientRect();
                if(r.width>50) out.push({x:r.left,y:r.top,w:r.width,h:r.height});
            });
            out.sort((a,b)=> (a.y-b.y) || (a.x-b.x));
            return out.slice(0,9);
        }""") or []
    except Exception as e:
        log("dump inner err " + str(e)); inner = []
    cells = []
    for b in inner:
        cx = int(b["x"] + b["w"]/2) + int(boxes["x"])
        cy = int(b["y"] + b["h"]/2) + int(boxes["y"])
        cells.append({"center": [cx, cy], "box": b})
    state["cells"] = len(cells)
    json.dump({"offset": boxes, "inner": inner, "cells": cells},
              open(COORDS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"dump: cells={len(cells)} offset={boxes}")
    # 逐格截图（带超时，越界容错）
    ok_shots = 0
    for i, b in enumerate(cells):
        cx, cy = b["center"]; cw, ch = b["box"]["w"], b["box"]["h"]
        x0 = max(0, int(cx-cw/2)); y0 = max(0, int(cy-ch/2))
        w = min(cw, 1280-x0); h = min(ch, 900-y0)
        if w <= 0 or h <= 0:
            continue
        try:
            await asyncio.wait_for(page.screenshot(path=CELLS[i], clip={"x": x0, "y": y0, "width": w, "height": h}), 15)
            ok_shots += 1
        except Exception as e:
            log(f"  shot {i} err {str(e)[:40]}")
    # 提交按钮
    try:
        sb = await _ev(cap, """() => {
            const el=document.querySelector('.vc-captcha-verify-pb')||document.querySelector('[class*=verify-pb]');
            if(!el) return null; const r=el.getBoundingClientRect();
            return {cx:r.left+r.width/2, cy:r.top+r.height/2};
        }""")
        state["submit"] = {"cx": int(sb["cx"]) + int(boxes["x"]), "cy": int(sb["cy"]) + int(boxes["y"])} if sb else None
    except Exception as e:
        state["submit"] = None
    log(f"dump ok: cells={len(cells)} shots={ok_shots} submit={state.get('submit')}")
    return len(cells) > 0

async def ensure_captcha(page):
    """若没墙就触发一次。每步带超时 + 心跳日志，绝不卡死。"""
    cap = await find_cap(page)
    if cap:
        log("captcha already on load"); return cap
    log("no captcha on load -> trigger 你好")
    try:
        box = page.locator("div.tiptap.ProseMirror").first
        await asyncio.wait_for(box.click(), 10)
        log("trigger: clicked composer")
        await asyncio.sleep(500)
        await page.keyboard.type("你好", delay=140)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        log("trigger: sent 你好, polling frame...")
    except Exception as e:
        log("trigger err: " + str(e))
    for i in range(20):
        cap = await find_cap(page)
        if cap:
            log(f"captcha frame appeared at poll {i}"); return cap
        await asyncio.sleep(1.2)
    log("no captcha frame after ~24s of polling")
    return await find_cap(page)

async def do_solve(idx):
    cd = json.load(open(COORDS, encoding="utf-8"))
    cells = cd["cells"]; ctx = server_ctx["ctx"]; page = server_ctx["page"]
    log("solve idx=" + str(idx))
    for i in idx:
        if i >= len(cells): continue
        cx, cy = cells[i]["center"]
        try:
            await page.mouse.move(cx, cy, steps=8); await asyncio.sleep(0.3)
            await page.mouse.click(cx, cy); await asyncio.sleep(0.4)
        except Exception as e:
            log(f"  click {i} err {e}")
    await asyncio.sleep(1.2)
    submitted = False
    sub = state.get("submit")
    if sub:
        try:
            await page.mouse.move(sub["cx"], sub["cy"], steps=6); await asyncio.sleep(0.3)
            await page.mouse.click(sub["cx"], sub["cy"]); submitted = True
        except Exception as e:
            log("  submit err " + str(e))
    log("submitted=" + str(submitted) + " waiting 15s")
    await asyncio.sleep(15)
    still = await find_cap(page) is not None
    try:
        await page.screenshot(path=AFTER, full_page=False)
    except Exception:
        pass
    tail = ""
    try:
        tail = (await page.evaluate("() => document.body ? document.body.innerText : ''"))[-300:]
    except Exception:
        pass
    log(f"done: captcha_still_present={still}")
    return {"idx": idx, "submitted": submitted, "captcha_still_present": still,
            "body_tail": tail, "shot": AFTER}

async def do_refresh():
    ctx = server_ctx["ctx"]; page = server_ctx["page"]
    cap = await find_cap(page)
    if cap:
        try:
            await cap.evaluate("() => { const el=document.querySelector('.vc-captcha-refresh'); if(el) el.click(); }")
        except Exception as e:
            log("refresh err " + str(e))
    await asyncio.sleep(3)
    await ensure_captcha(page)
    ok = await dump_cells(page)
    state["phase"] = "waiting_solve" if ok else "no_captcha"
    return {"cells": state.get("cells"), "prompt": state.get("prompt", "")[:200], "ok": ok}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, o, code=200):
        b = json.dumps(o, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._json({"phase": state["phase"], "msg": state["msg"], "cells": state.get("cells", 0),
                    "prompt": state.get("prompt", "")[:240], "ready": state.get("ready", False),
                    "cells_img": [os.path.basename(CELLS[i]) for i in range(9) if os.path.exists(CELLS[i])]})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        body = json.loads(raw) if raw else {}
        loop = server_ctx["loop"]
        if self.path == "/solve":
            fut = asyncio.run_coroutine_threadsafe(do_solve(body.get("idx", [])), loop)
            res = fut.result(timeout=90)
            res["phase"] = "solved_attempted"
            self._json(res)
        elif self.path == "/refresh":
            fut = asyncio.run_coroutine_threadsafe(do_refresh(), loop)
            res = fut.result(timeout=90)
            res["ok"] = True
            self._json(res)
        elif self.path == "/quit":
            server_ctx["quit"] = True
            self._json({"ok": True})
        else:
            self._json({"ok": True})

async def main():
    # 1) 先起 HTTP（控制面优先）
    loop = asyncio.get_running_loop()
    server_ctx["loop"] = loop
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever() if False else \
        threading.Thread(target=lambda: ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever(),
                         daemon=True).start()
    log("HTTP up on " + str(PORT))
    # 2) 浏览器步骤：包成有界任务，失败/超时也翻 ready（主控可 /refresh 重试）
    pw = await async_playwright().start()
    try:
        ctx = await asyncio.wait_for(pw.chromium.launch_persistent_context(
            PROFILE, headless=False, args=["--no-sandbox", "--disable-gpu"],
            viewport={"width": 1280, "height": 900}, ignore_default_args=["--enable-automation"]), 120)
    except Exception as e:
        log("ctx launch err: " + str(e))
        state["phase"] = "ctx_failed"; state["ready"] = True
        log("CTX FAILED - ready flag set, exiting")
        return
    server_ctx["ctx"] = ctx
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    server_ctx["page"] = page
    log("pages: " + str([p.url for p in ctx.pages]))
    try:
        await page.goto("https://www.doubao.com/chat/", timeout=45000, wait_until="domcontentloaded")
        await asyncio.sleep(4)
    except Exception as e:
        log("goto err " + str(e))
    try:
        await ensure_captcha(page)
        ok = await dump_cells(page)
        state["phase"] = "waiting_solve" if ok else "no_captcha"
    except Exception as e:
        log("browser init err: " + str(e))
        state["phase"] = "init_err"
    state["ready"] = True
    log(f"READY phase={state['phase']} cells={state.get('cells')}")
    # 3) 挂住直到 /quit
    while not server_ctx.get("quit"):
        await asyncio.sleep(1)
    try:
        await ctx.close(); await pw.stop()
    except Exception:
        pass
    log("QUIT")

if __name__ == "__main__":
    asyncio.run(main())
