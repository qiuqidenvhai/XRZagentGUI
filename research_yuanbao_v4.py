#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
元宝深度思考 UI 调研 v4 — 纯等待模式
策略：
- 不关任何弹窗
- 等你登录（每10s截图显示状态）
- 登录后立即保存cookies + 采集底部工具栏和深度思考按钮
- 最多等600s
用法：python -u research_yuanbao_v4.py
"""
import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

DATA_ROOT = Path(r"D:\软件\XianRenZhangAgent\xrz_data")
BROWSER_DATA_ROOT = DATA_ROOT / ".xianrenzhang_agent" / "browser_profiles"
YUANBAO_PROFILE = BROWSER_DATA_ROOT / "yuanbao"
LOG_FILE = Path(__file__).parent / "research_yuanbao_v4.log"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


async def capture_state(page, name="state"):
    try:
        await page.screenshot(path=str(Path(__file__).parent / f"yuanbao_{name}.png"))
        txt = await page.evaluate("() => document.body.innerText?.substring(0, 200) or ''")
        log(f"[{name}] 截图已保存 | body: {txt[:80]}")
    except Exception as e:
        log(f"[{name}] error: {e}")


async def save_all_cookies(browser):
    cookie_file = YUANBAO_PROFILE / "cookies.json"
    try:
        cookies = await browser.cookies()
        data = json.dumps(cookies, ensure_ascii=False, indent=2)
        cookie_file.write_text(data, encoding='utf-8')
        tokens = [c['name'] for c in cookies if any(k in c.get('name', '').lower()
                      for k in ['token', 'session', 'uid', 'uuid', 'sid', 'login', 'auth'])]
        log(f"Cookies saved: {len(cookies)} total, identity=[{','.join(tokens[:5])}]")
    except Exception as e:
        log(f"Save cookies failed: {e}")


async def check_logged_in(page):
    try:
        info = await page.evaluate(r"""() => {
            const body = document.body.innerText || '';
            const noNologin = !document.querySelectorAll('[class*="nologin"]').length;
            const editor = document.querySelector('[contenteditable], textarea');
            const hasInput = editor && editor.offsetWidth > 0 && editor.offsetHeight > 0;
            let ph = '';
            if (editor) ph = (editor.getAttribute('placeholder') || '').toLowerCase();
            let navOk = false;
            const navUser = document.querySelector('.yb-nav__user');
            if (navUser) {
                const t = navUser.innerText || '';
                if (!/未登录/.test(t) && /头像|昵称|用户|设置|退出/.test(t)) navOk = true;
                if (!t.includes('登录') && t.length > 5) navOk = true;
            }
            let toolOk = false;
            document.querySelectorAll('button, [role="button"], div[tabindex]').forEach(el => {
                if (toolOk) return;
                const r = el.getBoundingClientRect();
                if (r.top < 450 || r.height < 10) return;
                const text = (el.innerText || '').trim();
                if (/研究|创作|搜索|发送|快速|深入|录音|音乐|PPT|视频/.test(text)) toolOk = true;
            });
            const noLoginText = !body.includes('请登录')
                && !body.includes('微信登录')
                && !body.includes('扫码登录')
                && !body.includes('请使用微信');

            return {
                ok: (noNologin && hasInput && navOk && toolOk && noLoginText)
                    || (noNologin && hasInput && !ph.includes('登录') && toolOk),
                detail: `${noNologin}/${hasInput}/${navOk}/${toolOk}/${noLoginText}/ph="${ph.substring(0,20)}"`
            };
        }""")
        return info.get('ok', False), info.get('detail', '')
    except Exception:
        return False, "exception"


async def research_after_login(page):
    log("\n=== Post-login research ===")
    await capture_state(page, "logged_in")
    await asyncio.sleep(2)

    try:
        html = await page.evaluate(r"""() => {
            const ed = document.querySelector('[contenteditable].ql-editor, [contenteditable], textarea');
            if (!ed) return 'NO_EDITOR';
            let p = ed;
            for (let i = 0; i < 6; i++) { p = p.parentElement; if (!p) break; }
            if (!p) p = document.body;
            return p.outerHTML;
        }""")
        log(f"\n=== Input area parent HTML (first 8000 chars) ===")
        log(html[:8000])
    except Exception as e:
        log(f"HTML error: {e}")

    try:
        think_info = await page.evaluate(r"""() => {
            const results = [];
            const kws = ['deep','think','reasoning','深度','思考','深思','推理'];
            document.querySelectorAll('button, div[role="button"], div[tabindex], span, a, [class*="skill"]').forEach(el => {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
                const text = (el.innerText || '').trim().toLowerCase();
                const cls = (el.className || '').toString().toLowerCase();
                if (kws.some(k => (text + ' ' + cls).includes(k))) {
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 80),
                        className: (el.className || '').toString().substring(0, 200),
                        id: el.id || '',
                        attrs: JSON.stringify(Object.fromEntries(
                            Array.from(el.attributes || []).map(a => [a.name, (a.value||'').substring(0,30)])
                        )).substring(0, 300),
                    });
                }
            });
            return results;
        }""")
        log(f"\n=== Deep thinking elements ({len(think_info)}) ===")
        for t in think_info:
            log(f"  [{t['tag']}] text='{t['text']}' class='{t['className']}' "
                f"id='{t['id']}' attrs={t['attrs']}")

        if think_info:
            log("\n=== Try clicking deep thinking buttons ===")
            for t in think_info[:5]:
                text = t.get('text', '')
                if not text: continue
                log(f"  Click: {text}")
                try:
                    loc = page.locator(f"text='{text}'").first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=3000)
                        log(f"    OK")
                        await asyncio.sleep(1.5)
                        await capture_state(page, "click_1")
                    else:
                        log(f"    SKIP")
                except Exception as e:
                    log(f"    FAIL: {e}")
            await capture_state(page, "click_final")
    except Exception as e:
        log(f"Think search error: {e}")

    try:
        btns = await page.evaluate(r"""() => {
            const results = [];
            document.querySelectorAll('button, [role="button"], div[tabindex]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.top < 450 || rect.height < 10) return;
                const text = (el.innerText || '').trim();
                if (!text) return;
                results.push({
                    tag: el.tagName, text: text.substring(0, 60),
                    className: (el.className || '').toString().substring(0, 200),
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    w: Math.round(rect.width), h: Math.round(rect.height),
                });
            });
            return results;
        }""")
        log(f"\n=== Bottom visible buttons ({len(btns)}) ===")
        for b in btns:
            log(f"  [{b['tag']}] '{b['text']}' class='{b['className']}' "
                f"pos=({b['x']},{b['y']}) size={b['w']}x{b['h']}")
    except Exception as e:
        log(f"Buttons error: {e}")

    await capture_state(page, "final")
    log("=== Research complete ===")


async def main():
    log("=== Yuanbao research v4 start ===")
    log(f"profile: {YUANBAO_PROFILE}")

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    YUANBAO_PROFILE.mkdir(parents=True, exist_ok=True)

    ck = YUANBAO_PROFILE / "cookies.json"
    if ck.exists():
        log(f"Found cookies.json ({ck.stat().st_size} bytes)")
    else:
        log("No cookies.json - manual login required")

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-service-autorun",
        "--window-size=1280,860",
    ]

    log("Launching Chromium (persistent profile)...")
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=str(YUANBAO_PROFILE),
        headless=False,
        viewport={"width": 1280, "height": 860},
        args=launch_args,
        accept_downloads=True,
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    log("Opening Yuanbao...")
    await page.goto("https://yuanbao.tencent.com/chat/", wait_until="domcontentloaded", timeout=30000)

    log("\n" + "=" * 60)
    log("Please login to Yuanbao using WeChat/mobile/QQ in the Chrome window.")
    log("I will NOT close any popup. Screenshots every 10s.")
    log("Maximum wait: 600 seconds.")
    log("=" * 60)

    start = datetime.now()
    logged_in = False

    while True:
        elapsed = int((datetime.now() - start).total_seconds())

        if elapsed % 10 == 0 and elapsed > 0:
            log(f"  Waiting... {elapsed}s")
            await capture_state(page, f"wait_{elapsed}")

        if elapsed >= 600:
            log(f"\nTimeout after 600s")
            break

        is_logged, detail = await check_logged_in(page)
        if is_logged:
            log(f"\n[OK] Logged in detected ({elapsed}s) details: {detail}")
            logged_in = True
            break

        await asyncio.sleep(2)

    log("\n--- Saving cookies ---")
    await save_all_cookies(browser)

    if logged_in:
        await asyncio.sleep(3)
        await research_after_login(page)
        log("\n--- Final cookie save ---")
        await save_all_cookies(browser)
    else:
        log("Not logged in, skipping research")
        await capture_state(page, "not_logged")

    log(f"\n[{datetime.now()}] Done. Closing browser in 30s.")
    await asyncio.sleep(30)
    await browser.close()
    await pw.stop()
    log("Browser closed")


if __name__ == "__main__":
    asyncio.run(main())
