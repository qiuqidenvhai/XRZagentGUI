# -*- coding: utf-8 -*-
"""豆包 DOM 取证探针：独占启动豆包 profile，发一条普通消息，逐秒记录 DOM 演化。
运行前提：后端已关闭（否则 profile 被占用）。
证据输出：_probe_doubao_dom.jsonl（逐次快照）
"""
import asyncio, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent_core.xrz_paths import BROWSER_DATA_ROOT
from playwright.async_api import async_playwright

DOUBAO_DIR = BROWSER_DATA_ROOT / "doubao"
OUT = Path(__file__).parent / "_probe_doubao_dom.jsonl"

DUMP_JS = r"""() => {
  const out = {t: Date.now(), url: location.href};
  // 1) 候选容器：统计各种可能的类名前缀出现次数
  const probe = ['md-box-root','message-list','send-msg-bubble','receive','break-btn',
                 'message-block','chat-message','markdown','agent-chat','tts','flow-markdown'];
  out.cand = {};
  for (const p of probe) {
    out.cand[p] = document.querySelectorAll('[class*="'+p+'"]').length;
  }
  // 2) 输入框状态
  const inp = document.querySelector('div.tiptap.ProseMirror,[contenteditable],textarea');
  out.inputText = inp ? (inp.innerText||inp.value||'').slice(0,40) : null;
  // 3) md-box-root 元素的最后 3 个 innerText
  const mds = document.querySelectorAll('[class*="md-box-root"]');
  out.mdCount = mds.length;
  out.mdTail = [];
  for (let i = Math.max(0, mds.length-3); i < mds.length; i++) {
    out.mdTail.push((mds[i].innerText||'').replace(/\n+/g,' | ').slice(0,220));
  }
  // 4) 停止按钮（生成中标志）
  const stops = document.querySelectorAll('button,[role="button"]');
  out.stopBtns = [];
  for (const b of stops) {
    const al = (b.getAttribute('aria-label')||'');
    const cl = (typeof b.className === 'string' ? b.className : '');
    if (/stop|break|停止|中止/i.test(al + ' ' + cl)) {
      out.stopBtns.push({al: al, cl: cl.slice(0,60), vis: !!(b.offsetParent)});
    }
  }
  // 5) 类名直方图（前 40 个只出现一次的独特 class，帮助识别回复容器）
  const hist = {};
  document.querySelectorAll('div[class]').forEach(d => {
    const c = typeof d.className === 'string' ? d.className : '';
    c.split(/\s+/).forEach(x => { if (x) hist[x] = (hist[x]||0)+1; });
  });
  const entries = Object.entries(hist).filter(([k,v]) => v>=1);
  out.classTotal = entries.length;
  // 只保留看起来像「消息/回答」的类名
  out.msgLike = entries.filter(([k]) => /msg|message|bubble|answer|reply|md-box|markdown|content-|receive|send/i.test(k))
                        .sort((a,b)=>b[1]-a[1]).slice(0,30);
  // 6) 最后一条「消息块」候选的文本（用 message-list 的子元素）
  const ml = document.querySelector('[class*="message-list"]');
  if (ml) {
    out.mlChildren = ml.children.length;
    const last = ml.lastElementChild;
    out.mlLastText = last ? (last.innerText||'').replace(/\n+/g,' | ').slice(0,300) : null;
    out.mlLastCls = last ? (typeof last.className==='string'?last.className:'').slice(0,120) : null;
  }
  return out;
}"""


async def main():
    OUT.write_text("", encoding="utf-8")

    def snap(tag, obj):
        rec = {"tag": tag, **obj}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(DOUBAO_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.doubao.com/chat/", timeout=60000,
                        wait_until="domcontentloaded")
        await asyncio.sleep(8)
        snap("loaded", await page.evaluate(DUMP_JS))

        # 找输入框
        inp = page.locator("div.tiptap.ProseMirror, [contenteditable='true']").first
        if await inp.count() == 0:
            snap("no_input", {"html": (await page.content())[:3000]})
            await ctx.close()
            return
        await inp.click()
        await asyncio.sleep(0.5)
        # 用键盘输入（真实用户行为）
        await inp.type("你好，请用一句话介绍你自己", delay=30)
        await asyncio.sleep(1)
        snap("typed", await page.evaluate(DUMP_JS))

        # 发送：优先点可见的发送按钮，否则 Enter
        sent = False
        try:
            b = page.locator("button[aria-label*='发送'], [class*='send-btn'], [class*='sendBtn']").first
            if await b.count() > 0 and await b.is_visible():
                await b.click()
                sent = True
                snap("sent_by_button", {"ok": True})
        except Exception as e:
            snap("send_button_err", {"e": str(e)[:200]})
        if not sent:
            await page.keyboard.press("Enter")
            snap("sent_by_enter", {"ok": True})

        for i in range(24):   # 24 x 3s = 72s
            await asyncio.sleep(3)
            snap(f"t{(i+1)*3}s", await page.evaluate(DUMP_JS))

        await page.screenshot(path=str(Path(__file__).parent / "_probe_doubao.png"))
        await ctx.close()


asyncio.run(main())
