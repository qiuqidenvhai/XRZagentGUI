# 千问 send_message -> wait_response 全链路测试（走产品 PlatformBrowserManager 真实代码）
import asyncio, sys
from pathlib import Path
sys.path.insert(0, "D:/软件/XianRenZhangAgent")
from agent_core.platform_browser import PlatformBrowserManager

async def main():
    bm = PlatformBrowserManager("tongyi", headless=True)
    await bm.launch()
    page = bm._page
    for _ in range(4):
        try:
            await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=40000); break
        except Exception: await asyncio.sleep(3)
    await asyncio.sleep(5)
    print("URL:", page.url)

    base = await page.evaluate("() => document.querySelectorAll(\"[class*='message']\").length")
    print("基线消息数:", base)

    # 用产品真实 send_message（含 JS 设值修复 + CDP Enter 修复）
    sent = await bm.send_message("请只回复四个字：链路畅通")
    print("send_message 返回:", sent)
    await asyncio.sleep(4)
    cnt = await page.evaluate("() => document.querySelectorAll(\"[class*='message']\").length")
    print(f"发送后消息数 {base}->{cnt} => {'✅消息已发出' if cnt > base else '❌未发出'}")

    if cnt > base:
        # 用产品真实 wait_response 抓回复
        reply = await bm.wait_response(timeout=60)
        print("wait_response 回复:", repr((reply or "")[:150]))
        ok = bool(reply and reply.strip() and "未收到回复" not in reply)
        print("全链路判定:", "✅ PASS" if ok else "❌ FAIL")
    else:
        print("全链路判定: ❌ FAIL (消息没发出去)")

    await bm._playwright.stop()

asyncio.run(main())
