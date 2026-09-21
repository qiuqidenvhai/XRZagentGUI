# -*- coding: utf-8 -*-
"""决定性实验：有头 chromium-1217 在 4 组参数下，哪组能出活 page + 能 goto。
用独立临时目录，不抢后端(4192)的 deepseek 锁。"""
import asyncio, json, os, tempfile, time

CHROME = r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers\chromium-1217\chrome-win64\chrome.exe"
PW_PATH = r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PW_PATH

CASES = {
    "A_minimal": ["--no-first-run", "--no-service-autorun"],
    "B_minimal_no_sandbox": ["--no-first-run", "--no-service-autorun", "--no-sandbox"],
    "C_browser_py_args": ["--no-first-run", "--no-service-autorun", "--no-sandbox",
                          "--disable-gpu", "--disable-dev-shm-usage"],
    "D_no_sandbox_nogpu_off": ["--no-first-run", "--no-service-autorun",
                                "--no-sandbox", "--disable-dev-shm-usage"],
}

async def try_case(name, args):
    from playwright.async_api import async_playwright
    tmp = tempfile.mkdtemp(prefix=f"xrz_test_{name}_")
    res = {"name": name, "args": args, "tmp": tmp}
    pw = None
    ctx = None
    try:
        pw = await async_playwright().start()
        t0 = time.time()
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=tmp, headless=False,
            viewport={"width": 1280, "height": 800}, args=args,
            timeout=25000,
        )
        res["launch_ok"] = True
        res["launch_ms"] = int((time.time()-t0)*1000)
        res["pages_initial"] = len(ctx.pages)
        # 活 page 判定：取/建一个 page，能 evaluate 出 1+1=2 才算活
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            r = await page.evaluate("1+1")
            res["page_live"] = (r == 2)
        except Exception as e:
            res["page_live"] = False
            res["page_err"] = str(e)[:120]
        # goto 测试
        try:
            await page.goto("about:blank", timeout=8000)
            res["goto_ok"] = True
        except Exception as e:
            res["goto_ok"] = False
            res["goto_err"] = str(e)[:120]
    except Exception as e:
        res["launch_ok"] = False
        res["launch_err"] = str(e)[:300]
    finally:
        try: await ctx.close()
        except Exception: pass
        try: await pw.stop()
        except Exception: pass
    return res

async def main():
    results = []
    for name, args in CASES.items():
        results.append(await try_case(name, args))
    print(json.dumps(results, ensure_ascii=False, indent=1))

asyncio.run(main())
