"""
probe_ui_signal.py — 实地探测 qwen / deepseek / doubao 的「生成中 → 生成完成」UI 信号

目的：不靠猜。真实打开网站、发测试消息，全程采样输入框附近的「动作按钮」
（发送 <-> 停止 的切换）与各种「生成中」标记元素，把每一帧的关键 DOM 属性
dump 出来，确定「AI 是否还在输出」的可靠判据。

本轮改进（上一版踩的坑）：
  - qwen 的停止控件【没有】"停止/stop" 文本，不能靠文本匹配；
  - 「离输入框最近的按钮」会误抓麦克风键（record-btn）；
  → 因此改为：在生成过程的关键时刻，dump【全部】按钮 + 【全部】带
    stop/pause/generat/loading/thinking/stream/typing 类名的元素，
    由我们人工对照 DOM 找出真正的「生成中」标记。

用法：
    python probe_ui_signal.py tongyi
    python probe_ui_signal.py deepseek
    python probe_ui_signal.py tongyi --nosend   # 只看空闲态
"""
import asyncio
import sys
import json
import time
from pathlib import Path

from agent_core.xrz_paths import BROWSER_DATA_ROOT

# msg_sel：与 platform_browser.PLATFORM_PROFILES 中 response_selector 对齐
PROFILES = {
    "tongyi": {
        "url": "https://chat.qwen.ai/",
        "name": "通义千问Qwen",
        "msg_sel": "[class*='message'], [data-testid*='message'], main [class*='chat']",
    },
    "deepseek": {
        "url": "https://chat.deepseek.com",
        "name": "DeepSeek",
        "msg_sel": "[class*='message']",
    },
    "doubao": {
        "url": "https://www.doubao.com/chat/",
        "name": "豆包",
        "msg_sel": "[class*='message'], [class*='answer']",
    },
}

# 紧凑时间线用的 dump：动作按钮 + stop 文本 + 消息总长
DUMP_JS = r"""
() => {
    const out = { actionBtn: null, actionDist: null, hasStopText: false,
                  totalMsgLen: 0, msgCount: 0, url: location.href };
    const inputs = Array.from(document.querySelectorAll("textarea, [contenteditable='true']"));
    let inputRect = null;
    for (const el of inputs) { const r = el.getBoundingClientRect(); if (r.width && r.height) inputRect = r; }
    const btns = Array.from(document.querySelectorAll("button, [role='button']"));
    let bestBtn = null, bestDist = Infinity;
    for (const b of btns) {
        const rect = b.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const aria = (b.getAttribute('aria-label') || '').trim();
        const testid = (b.getAttribute('data-testid') || '').trim();
        const cls = (b.className && b.className.toString) ? b.className.toString().slice(0, 140) : '';
        const txt = (b.innerText || '').trim().slice(0, 30);
        const disabled = b.disabled === true || b.getAttribute('aria-disabled') === 'true';
        const svg = b.querySelector('svg');
        let svgHint = '';
        if (svg) { const inner = svg.innerHTML.toLowerCase();
            if (inner.includes('<rect')) svgHint += 'rect ';
            if (inner.includes('stop')) svgHint += 'stop ';
            if (inner.includes('arrow') || inner.includes('path')) svgHint += 'path ';
            svgHint += 'shapes:' + svg.children.length; }
        const entry = { aria, testid, cls, txt, disabled, svgHint,
                        x: Math.round(rect.x), y: Math.round(rect.y) };
        const blob = (aria + ' ' + testid + ' ' + txt).toLowerCase();
        if (blob.includes('stop') || blob.includes('停止') || blob.includes('停')) out.hasStopText = true;
        if (inputRect) {
            const cx = rect.x + rect.width/2, cy = rect.y + rect.height/2;
            const d = Math.hypot(cx - inputRect.right, cy - inputRect.bottom);
            if (d < bestDist) { bestDist = d; bestBtn = entry; }
        }
    }
    out.actionBtn = bestBtn; out.actionDist = inputRect ? Math.round(bestDist) : null;
    const msgs = document.querySelectorAll("MSGSEL");
    let total = 0; for (const m of msgs) { const t = (m.innerText||'').trim(); if (t) total += t.length; }
    out.totalMsgLen = total; out.msgCount = msgs.length;
    return out;
}
""".replace("MSGSEL", "__MSGSEL__")

# 详细 dump：全部按钮 + 全部「生成中」标记元素（关键帧用，用来找真实信号）
DUMP_DETAIL_JS = r"""
() => {
    const res = { buttons: [], markers: [], url: location.href };
    const inputs = Array.from(document.querySelectorAll("textarea, [contenteditable='true']"));
    let inputRect = null;
    for (const el of inputs) { const r = el.getBoundingClientRect(); if (r.width && r.height) inputRect = r; }
    const btns = Array.from(document.querySelectorAll("button, [role='button']"));
    for (const b of btns) {
        const rect = b.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        res.buttons.push({
            tag: b.tagName,
            aria: (b.getAttribute('aria-label') || '').trim(),
            testid: (b.getAttribute('data-testid') || '').trim(),
            cls: (b.className && b.className.toString) ? b.className.toString().slice(0, 160) : '',
            txt: (b.innerText || '').trim().slice(0, 20),
            disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true',
            x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width)
        });
    }
    const markerSel = "[class*='stop'],[class*='pause'],[class*='generat'],[class*='loading'],"
                    + "[class*='thinking'],[class*='stream'],[class*='typing'],[class*='sending'],"
                    + "[class*='spinner'],[class*='pending']";
    const ms = document.querySelectorAll(markerSel);
    for (const m of ms) {
        const rect = m.getBoundingClientRect();
        res.markers.push({
            tag: m.tagName,
            cls: (m.className && m.className.toString) ? m.className.toString().slice(0, 160) : '',
            aria: (m.getAttribute('aria-label') || '').trim(),
            txt: (m.innerText || '').trim().slice(0, 20),
            x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)
        });
    }
    res.inputRect = inputRect ? {x:Math.round(inputRect.x), y:Math.round(inputRect.y),
                                 w:Math.round(inputRect.width), h:Math.round(inputRect.height)} : null;
    return res;
}
"""


def make_dump_js(msg_sel: str) -> str:
    return DUMP_JS.replace("__MSGSEL__", msg_sel)


def fmt_btn(b):
    if not b:
        return "无"
    return (f"aria='{b['aria']}' txt='{b['txt']}' disabled={b['disabled']} "
            f"svg=[{b['svgHint']}] cls='{b['cls'][:50]}'")


def print_detail(snap, label):
    print(f"\n----- 详细快照 [{label}] -----")
    ir = snap.get("inputRect")
    print(f"  输入框位置: {ir}")
    print(f"  按钮({len(snap['buttons'])}个):")
    for b in snap["buttons"]:
        print(f"    <{b['tag']}> aria='{b['aria']}' testid='{b['testid']}' "
              f"txt='{b['txt']}' disabled={b['disabled']} "
              f"pos=({b['x']},{b['y']},{b['w']}) cls='{b['cls'][:80]}'")
    print(f"  生成中标记元素({len(snap['markers'])}个):")
    for m in snap["markers"]:
        print(f"    <{m['tag']}> aria='{m['aria']}' txt='{m['txt']}' "
              f"pos=({m['x']},{m['y']},{m['w']}x{m['h']}) cls='{m['cls'][:90]}'")


async def probe(platform: str, send_test: bool):
    prof = PROFILES[platform]
    udd = BROWSER_DATA_ROOT / platform
    dump_js = make_dump_js(prof["msg_sel"])
    from playwright.async_api import async_playwright

    outdir = Path("probe_out")
    outdir.mkdir(exist_ok=True)
    log = []

    pw = await async_playwright().start()
    for p in udd.glob("Singleton*"):
        try:
            p.unlink()
        except Exception:
            pass

    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(udd), headless=False, viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(prof["url"], wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(6)

    print(f"\n===== {platform}({prof['name']}) 初始 UI 快照 =====")
    snap0 = await page.evaluate(dump_js)
    print("URL:", snap0["url"])
    print(f"空闲态: 动作按钮({snap0['actionDist']}px)={fmt_btn(snap0['actionBtn'])} "
          f"hasStopText={snap0['hasStopText']} msgCount={snap0['msgCount']} totalMsgLen={snap0['totalMsgLen']}")
    print_detail(await page.evaluate(DUMP_DETAIL_JS), "空闲")
    await page.screenshot(path=str(outdir / f"{platform}_00_idle.png"))
    log.append({"phase": "idle", "snap": snap0})

    if not send_test:
        await ctx.close(); await pw.stop()
        (outdir / f"{platform}_probe.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    print("\n----- 发送测试消息，采样生成过程 -----")
    input_sel = "textarea, [contenteditable='true']"
    try:
        await page.wait_for_selector(input_sel, timeout=10000)
    except Exception as e:
        print("未找到输入框:", e); await ctx.close(); await pw.stop(); return

    box = page.locator(input_sel).first
    await box.click(); await asyncio.sleep(0.2)
    await box.fill("请用大约150字介绍一下仙人掌这种植物，然后换行输出三个要点。")
    await asyncio.sleep(0.3)
    await box.press("Enter")
    send_ts = time.time()
    print("已发送，开始每 0.5s 采样...\n")

    # 关键帧：生成初期 / 中期 / 后期 / 疑似完成 / 充分完成
    detail_ticks = {3: "生成初期~1.5s", 10: "生成中~5s", 22: "生成中~11s",
                    40: "后期~20s", 98: "最终~49s"}
    seen_detail = set()

    prev_stop = None
    prev_act = None
    transitions = []
    for i in range(100):
        await asyncio.sleep(0.5)
        try:
            snap = await page.evaluate(dump_js)
        except Exception:
            continue
        t = round(time.time() - send_ts, 1)
        act = snap["actionBtn"]
        act_key = fmt_btn(act)
        has_stop = snap["hasStopText"]
        mlen = snap["totalMsgLen"]

        if act_key != prev_act:
            transitions.append({"t": t, "type": "action", "state": act_key, "stopText": has_stop, "msgLen": mlen})
            print(f"  [t={t}s] 动作按钮 -> {act_key}")
            prev_act = act_key
        if has_stop != prev_stop:
            transitions.append({"t": t, "type": "stopText", "state": has_stop, "action": act_key, "msgLen": mlen})
            print(f"  [t={t}s] hasStopText -> {has_stop}  (动作: {act_key})")
            prev_stop = has_stop
        if i % 10 == 0:
            print(f"  [t={t}s] stopText={has_stop} msgLen={mlen} action=({fmt_btn(act)})")

        if i in detail_ticks and i not in seen_detail:
            seen_detail.add(i)
            d = await page.evaluate(DUMP_DETAIL_JS)
            print_detail(d, detail_ticks[i])
            log.append({"phase": "detail", "tick": i, "t": t, "detail": d})
        log.append({"phase": "gen", "t": t, "stopText": has_stop, "actionBtn": act, "msgLen": mlen})
        if i in (2, 30, 60, 99):
            await page.screenshot(path=str(outdir / f"{platform}_gen_{i:03d}.png"))

    print("\n===== 关键状态变化时间线 =====")
    for e in transitions:
        print(f"  t={e['t']}s [{e['type']}] -> {e['state']}")
    print(f"\n最终: hasStopText={prev_stop} msgLen={snap.get('totalMsgLen')} "
          f"动作按钮=({fmt_btn(snap.get('actionBtn'))})")
    (outdir / f"{platform}_probe.json").write_text(
        json.dumps({"transitions": transitions, "timeline": log}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n结果已存 probe_out/{platform}_probe.json")
    await ctx.close(); await pw.stop()


if __name__ == "__main__":
    platform = sys.argv[1] if len(sys.argv) > 1 else "tongyi"
    send = "--nosend" not in sys.argv
    asyncio.run(probe(platform, send))
