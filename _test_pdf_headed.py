# -*- coding: utf-8 -*-
"""实测：有头(headed) Chromium 下 page.pdf() 到底能不能用。

用临时 profile（绝不动 DeepSeek 的持久化目录），分别试有头/无头两种模式。
"""
import sys, os, asyncio
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\软件\XianRenZhangAgent")
BROWSERS = str(ROOT / "xrz_data" / "playwright_browsers")
OUT = ROOT / "xrz_data" / "XianRenZhang_tasks" / "_pdftest"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS

HTML = "<h1>PDF 测试</h1><p>中文内容测试：仙人掌 Agent 网页交互留痕</p>"


async def try_mode(headless: bool):
    from playwright.async_api import async_playwright
    tag = "headless" if headless else "headed"
    prof = ROOT / "xrz_data" / f"_tmp_profile_{tag}"
    prof.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(prof),
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as e:
            print(f"[{tag}] 启动失败: {type(e).__name__}: {e}")
            return False
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.set_content(HTML)
            f = OUT / f"test_{tag}.pdf"
            try:
                await page.pdf(path=str(f), format="A4")
                ok = f.exists() and f.stat().st_size > 0
                print(f"[{tag}] page.pdf() -> {'成功' if ok else '产出为空'} "
                      f"size={f.stat().st_size if f.exists() else 0}")
            except Exception as e:
                print(f"[{tag}] page.pdf() 失败: {type(e).__name__}: {str(e)[:300]}")
                ok = False
            # 对照：截图一定可用
            s = OUT / f"test_{tag}.png"
            try:
                await page.screenshot(path=str(s), full_page=True)
                print(f"[{tag}] screenshot -> 成功 size={s.stat().st_size}")
            except Exception as e:
                print(f"[{tag}] screenshot 失败: {e}")
            return ok
        finally:
            try:
                await ctx.close()
            except Exception:
                pass


async def main():
    print("=== 有头(headed) ===")
    h1 = await try_mode(False)
    print("=== 无头(headless) ===")
    h2 = await try_mode(True)
    print()
    print(f"结论: headed={'可用' if h1 else '不可用'} / headless={'可用' if h2 else '不可用'}")


if __name__ == "__main__":
    asyncio.run(main())
