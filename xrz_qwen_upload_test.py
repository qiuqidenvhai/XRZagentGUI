import asyncio, sys
from pathlib import Path
sys.path.insert(0, "D:/软件/XianRenZhangAgent")
from agent_core.platform_browser import PlatformBrowserManager

TEST_FILE = Path("D:/软件/XianRenZhangAgent/test_output/_qwen_probe_sample.pdf")

async def main():
    bm = PlatformBrowserManager("tongyi", headless=True)
    bm.user_data_dir = Path("D:/软件/XianRenZhangAgent/_qwen_probe_profile")
    await bm.launch()
    page = bm._page
    for _ in range(4):
        try:
            await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=40000); break
        except Exception: await asyncio.sleep(3)
    await asyncio.sleep(5)

    # 直接用修复后的 upload_files 逻辑（走平台 profile 的两步流程）
    res = await bm.upload_files([str(TEST_FILE)])
    print("upload_files 返回:", res)

    # 验证页面确有文件卡片
    ok = await page.evaluate("""(fname) => {
        const body=document.body.innerText.toLowerCase().replace(/\\s+/g,'');
        const fn=fname.toLowerCase().replace(/\\s+/g,'');
        const prev=document.querySelector('[class*="file-card"], [class*="fileitem"], [class*="message-input-column-file"]');
        return { bodyHas: body.includes(fn), hasPreview: !!prev };
    }""", TEST_FILE.name)
    print("页面验证:", ok)
    await bm._playwright.stop()

asyncio.run(main())
