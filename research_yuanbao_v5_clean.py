#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
元宝深度思考 UI 调研 v5 Clean — 简化版，确保等待用户登录
策略：
1. 清理残留进程
2. 启动 Chromium（持久化 profile，自动加载已有 cookies）
3. goto 元宝 chat 页
4. 检查是否已登录：
   - 有 login 按钮 / nologin class → 未登录，截图等待用户
   - 否则 → 已登录，采集 UI + 深度思考按钮
5. 等 300 秒后关闭（或用户在浏览器完成登录后自动继续）
"""
import asyncio
import sys
import json
from pathlib import Path
from datetime import datetime

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

DATA_ROOT = Path(r"D:\软件\XianRenZhangAgent\xrz_data")
BROWSER_DATA_ROOT = DATA_ROOT / ".xianrenzhang_agent" / "browser_profiles"
YUANBAO_PROFILE = BROWSER_DATA_ROOT / "yuanbao"
LOG_FILE = WORK_DIR / "research_yuanbao_v5.log"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


async def capture_state(page, name="state"):
    try:
        await page.screenshot(path=str(WORK_DIR / f"yuanbao_{name}.png"))
        txt = await page.evaluate("() => document.body.innerText?.substring(0, 200) or ''")
        log(f"[{name}] screenshot saved | body: {txt[:100]}")
    except Exception as e:
        log(f"[{name}] error: {e}")


async def save_all_cookies(browser):
    cookie_file = YUANBAO_PROFILE / "cookies.json"
    try:
        cookies = await browser.cookies()
        data = json.dumps(cookies, ensure_ascii=False, indent=2)
        cookie_file.write_text(data, encoding='utf-8')
        tokens = [c['name'] for c in cookies if any(k in c.get('name', '').lower()
                      for k in ['token', 'session', 'uid', 'uuid', 'sid', 'login', 'auth', 'user'])]
        log(f"Cookies saved: {len(cookies)} total, identity=[{','.join(tokens[:8])}]")
    except Exception as e:
        log(f"Save cookies failed: {e}")


async def simple_check_logged_in(page):
    """极简检查：是否看到登录入口。没看到就认为已登录。"""
    try:
        info = await page.evaluate(r"""() => {
            const body = document.body.innerText || '';
            // 1. 检查是否有登录按钮（已登录时应该没有）
            const loginBtns = document.querySelectorAll('button:has-text("登录"), a:has-text("登录"), button:has-text("Login")');
            const hasLoginBtn = loginBtns.length > 0;
            
            // 2. 检查是否有 nologin 类
            const hasNologin = document.querySelectorAll('[class*="nologin"]').length > 0;
            
            // 3. 检查二维码/微信扫码特征
            const qrCode = document.querySelector('[class*="qr"], [class*="QR"], img[src*="qr"], img[src*="qrcode"]');
            const hasQr = qrCode !== null;
            
            // 4. 输入框是否可用（placeholder不含登录）
            const editor = document.querySelector('[contenteditable], textarea');
            const hasInput = editor && editor.offsetWidth > 0 && editor.offsetHeight > 0;
            let ph = '';
            if (editor) ph = (editor.getAttribute('placeholder') || '').toLowerCase();
            const inputPhOk = !ph.includes('登录') && !ph.includes('请输入');
            
            // 已登录条件：无登录按钮 + 无nologin + 无二维码 + 输入框可用且placeholder不含登录
            const isLogged = !hasLoginBtn && !hasNologin && !hasQr && hasInput && inputPhOk;
            
            return {
                logged_in: isLogged,
                detail: `loginBtn=${hasLoginBtn}/nologin=${hasNologin}/qr=${hasQr}/input=${hasInput}/ph="${ph.substring(0,20)}"`
            };
        }""")
        return info.get('logged_in', False), info.get('detail', '')
    except Exception:
        return False, "exception"


async def research_after_login(page):
    log("\n=== Post-login research ===")
    await capture_state(page, "logged_in")
    await asyncio.sleep(2)

    # 底部输入区HTML
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

    # 深度思考相关元素
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

    # 底部可见按钮列表
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
    log("=== Yuanbao research v5 clean start ===")
    log(f"profile: {YUANBAO_PROFILE}")

    from playwright.async_api import async_playwright

    # 清理残留进程
    import subprocess
    try:
        subprocess.run(["taskkill", "/f", "/im", "chromium.exe"], capture_output=True)
        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
    except: pass

    YUANBAO_PROFILE.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-service-autorun",
        "--window-size=1280,860",
    ]

    log("Launching Chromium (persistent profile with existing cookies)...")
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

    # 等待页面稳定
    await asyncio.sleep(5)

    is_logged, detail = await simple_check_logged_in(page)
    if is_logged:
        log(f"\n[OK] Already logged in! Details: {detail}")
        await asyncio.sleep(3)

        # 保存 cookies
        log("\n--- Saving cookies ---")
        await save_all_cookies(browser)

        # 调研 UI
        await research_after_login(page)

        # 再次保存
        log("\n--- Final cookie save ---")
        await save_all_cookies(browser)
    else:
        log(f"\n[NO] Not logged in yet. Details: {detail}")
        await capture_state(page, "not_logged")
        log("\nWaiting for user login (up to 300 seconds)...")
        
        # 每 30 秒检查一次登录状态
        for i in range(10):
            await asyncio.sleep(30)
            is_logged, detail = await simple_check_logged_in(page)
            if is_logged:
                log(f"\n[OK] User logged in! Details: {detail}")
                await capture_state(page, "after_login")
                await save_all_cookies(browser)
                await research_after_login(page)
                break
            else:
                log(f"  Check {i+1}: still not logged in...")
        
        # 如果一直没登录，至少保存当前 cookies（可能部分有效）
        if not is_logged:
            log("\nTimeout waiting for login. Saving whatever cookies exist...")
            await save_all_cookies(browser)
            await capture_state(page, "timeout_not_logged")

    log(f"\n[{datetime.now()}] Done. Closing browser in 30s.")
    await asyncio.sleep(30)
    await browser.close()
    await pw.stop()
    log("Browser closed")


if __name__ == "__main__":
    asyncio.run(main())
