"""
validate_wait_qwen.py — 用真实 qwen 站点验证「UI 信号驱动」的 wait_response

做法：
  1. 启动 tongyi 持久化浏览器（复用已登录 profile）
  2. 发一条会触发流式多段回复的消息
  3. 并发监控 .stop-button 的出现/消失 与 回复文本增长时间线
  4. 调用【真实的】PlatformBrowserManager.wait_response(timeout=120)
  5. 打印：停止按钮何时出现/消失、最终文本长度、wait_response 耗时
     —— 若 wait_response 在 .stop-button 消失后才返回、且拿到了完整文本，
        即证明检测已从「靠等待时间」改为「靠浏览器 UI 信号」。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent_core.platform_browser import PlatformBrowserManager

GEN_JS = ("(sel) => { const els = document.querySelectorAll(sel);"
          " for (const el of els) { const r = el.getBoundingClientRect();"
          " if (r.width > 0 && r.height > 0) return true; } return false; }")
TXT_JS = ("() => { const els = document.querySelectorAll(\"[class*='message'],"
          " [data-testid*='message'], main [class*='chat']\");"
          " let n=0; for (const m of els){ const t=(m.innerText||'').trim(); if(t) n+=t.length; }"
          " return n; }")


async def monitor(bm, stop_event, timeline):
    t0 = time.time()
    while not stop_event.is_set():
        try:
            stop = await bm._page.evaluate(GEN_JS, ".stop-button")
            txt = await bm._page.evaluate(TXT_JS)
            timeline.append((round(time.time() - t0, 1), bool(stop), txt))
        except Exception:
            pass
        await asyncio.sleep(0.4)


async def main():
    bm = PlatformBrowserManager("tongyi", headless=False)
    await bm.launch()
    await bm._page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(5)

    prompt = "请用三句话介绍仙人掌这种植物，然后换行输出一个包含『名称/产地/特点』三个字段的 JSON 要点。"
    print(">>> 发送消息:", prompt[:30], "...")
    await bm.send_message(prompt)

    stop_event = asyncio.Event()
    timeline = []
    mon = asyncio.create_task(monitor(bm, stop_event, timeline))

    t0 = time.time()
    resp = await bm.wait_response(timeout=120)
    cost = round(time.time() - t0, 1)
    stop_event.set()
    await mon

    # 统计 stop-button 时间线关键节点
    first_stop = next((r for r in timeline if r[1]), None)
    last_stop = next((r for r in reversed(timeline) if r[1]), None)
    max_len = max((r[2] for r in timeline), default=0)
    print("\n===== 验证结果 =====")
    print(f"wait_response 耗时: {cost}s")
    print(f"返回文本长度: {len(resp) if resp else 0}")
    print(f"监控到 .stop-button 首次出现: t={first_stop[0]}s" if first_stop else "  未监测到 stop-button")
    print(f"监控到 .stop-button 最后出现: t={last_stop[0]}s" if last_stop else "  (无)")
    print(f"回复文本最大长度(监控): {max_len}")
    print(f"返回内容是否以未闭合标记结尾: {resp.rstrip().endswith('@@@@') if resp else 'N/A'}")
    print("\n--- .stop-button 时间线(每 ~0.4s) ---")
    for r in timeline[::5]:
        print(f"  t={r[0]}s stop={r[1]} txtLen={r[2]}")
    if resp:
        print("\n--- 返回文本前 300 字 ---")
        print(resp[:300])

    await bm._playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
