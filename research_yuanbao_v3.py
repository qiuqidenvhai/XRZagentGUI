#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""元宝深度思考 UI 调研（v3）

策略完全改变：
- 不自动关闭登录弹窗
- 弹浏览器后只 goto 到 yuanbao 首页，给用户充分时间登录
- 等用户发「登好了」或 600s 后自动检测
- 登录后保存 cookies 到 D 盘 profile + 调 send_message.save_cookies()
- 然后采集底部工具栏 HTML、深度思考按钮选择器

核心：你扫你的码，我不动手。
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

LOG_FILE = Path(__file__).parent / "research_yuanbao_v3.log"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


async def check_logged_in(page) -> bool:
    """综合信号检测登录状态。"""
    try:
        info = await page.evaluate(r"""() => {
            const body = document.body.innerText || '';
            // 条件 A: nologin 完全消失
            const noNologin = !document.querySelectorAll('[class*="nologin"]').length;
            // 条件 B: 有可用的输入框
            const editor = document.querySelector('[contenteditable], textarea');
            const hasInput = editor && editor.offsetWidth > 0 && editor.offsetHeight > 0;
            // 条件 C: placeholder 不再含"登录"
            let ph = '';
            if (editor) ph = (editor.getAttribute('placeholder') || '').toLowerCase();
            // 条件 D: 底部有功能按钮文字
            let hasToolbarBtns = false;
            document.querySelectorAll('button, [role="button"], div[tabindex]').forEach(el => {
                if (hasToolbarBtns) return;
                const rect = el.getBoundingClientRect();
                if (rect.top < 450 || rect.height < 10) return;
                const text = (el.innerText || '').trim();
                if (/研究|创作|搜索|发送|快速|深入|录音|音乐|PPT|视频/.test(text)) hasToolbarBtns = true;
            });
            // 条件 E: 右上角导航区
            const navUser = document.querySelector('.yb-nav__user, header [class*=user]');
            let navOk = false;
            if (navUser) {
                const t = navUser.innerText || '';
                if (!/未登录/.test(t) && /头像|昵称|用户|设置|退出/.test(t)) navOk = true;
            }
            // 条件 F: body 不再含明确未登录提示
            const noLoginText = !body.includes('请登录')
                && !body.includes('微信登录')
                && !body.includes('扫码登录')
                && !body.includes('请使用微信')
                && !body.includes('登录后');

            return {
                noNologin, hasInput, ph: ph.substring(0, 50),
                hasToolbarBtns, navOk, noLoginText,
                ok: (noNologin && hasInput && hasToolbarBtns && (navOk || noLoginText))
                    || (noNologin && hasInput && !ph.includes('登录') && hasToolbarBtns),
            };
        }""")
        return info.get('ok', False)
    except Exception:
        return False


async def save_all_cookies(browser):
    """把 Playwright browser.cookies() + IndexedDB 登录态都保存下来。"""
    # 方式1: Playwright 的 cookies
    cookie_file = YUANBAO_PROFILE / "cookies.json"
    try:
        cookies = await browser.cookies()
        data = json.dumps(cookies, ensure_ascii=False, indent=2)
        cookie_file.write_text(data, encoding='utf-8')
        tokens = [c['name'] for c in cookies if any(k in c.get('name','').lower()
                      for k in ['token', 'session', 'uid', 'uuid', 'sid', 'login', 'auth'])]
        log(f"Cookies 已保存到 {cookie_file}（共 {len(cookies)} 条，身份类: {tokens[:10]}）")
    except Exception as e:
        log(f"Playwright cookies 保存失败: {e}")

    # 方式2: 检查 Chromium 自身持久化 profile 的 IndexedDB
    idx_base = YUANBAO_PROFILE / "Default" / "IndexedDB" / "https_yuanbao.tencent.com_0.indexeddb.leveldb"
    db_files = list(idx_base.glob('*.log')) if idx_base.exists() else []
    log(f"IndexedDB https_yuanbao.tencent.com: {len(db_files)} files")

    # 方式3: 用 platform_browser.py 里已有方法保存
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from agent_core.platform_browser import BrowserManager
        bm = BrowserManager(headless=False)
        await bm.launch("yuanbao")
        if await bm.check_login():
            await bm.save_cookies()
            log("BrowserManager.save_cookies() 也确认保存成功")
        await bm.close()
    except Exception as e:
        log(f"BrowserManager 方式保存失败: {e}")


async def research_after_login(page):
    """采集底部工具栏 HTML 和深度思考按钮。"""
    log("\n=== 登录后调研开始 ===")

    # 截图
    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_v3_logged.png"))
    log("截图: yuanbao_v3_logged.png")
    await asyncio.sleep(2)

    all_text = await page.evaluate("() => document.body.innerText?.substring(0, 500) || ''")
    log(f"页面文本开头: {all_text[:200]}")

    # 抓取底部输入区父容器完整 HTML
    try:
        html = await page.evaluate(r"""() => {
            const editor = document.querySelector(
                '[contenteditable].ql-editor, [contenteditable], textarea'
            );
            if (!editor) return 'NO_EDITOR_FOUND';
            let p = editor;
            for (let i = 0; i < 6; i++) {
                p = p.parentElement;
                if (!p) break;
            }
            if (!p) p = document.body;
            return p.outerHTML;
        }""")
        log(f"\n=== 底部输入区父容器 HTML（前 10000 字符）===")
        log(html[:10000])
    except Exception as e:
        log(f"HTML 抓取异常: {e}")

    # 宽泛搜索深度思考相关元素
    try:
        think_info = await page.evaluate(r"""() => {
            const results = [];
            const keywords = ['deep', 'think', 'reasoning', '深度', '思考', '深思', '推理'];
            document.querySelectorAll('button, div[role="button"], div[tabindex], span, a, [class*="skill"]').forEach(el => {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
                const text = (el.innerText || '').trim().toLowerCase();
                const cls = (el.className || '').toString().toLowerCase();
                const combined = text + ' ' + cls;
                if (keywords.some(k => combined.includes(k))) {
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 80),
                        className: (el.className || '').toString().substring(0, 200),
                        id: el.id || '',
                        attrs: JSON.stringify(Object.fromEntries(
                            Array.from(el.attributes || []).map(a => [a.name, (a.value || '').substring(0, 30)])
                        )).substring(0, 300),
                    });
                }
            });
            return results;
        }""")
        log(f"\n=== 深度思考相关元素（{len(think_info)}个）===")
        for t in think_info:
            log(f"  [{t['tag']}] text='{t['text']}' class='{t['className']}' "
                f"id='{t['id']}' attrs={t['attrs']}")

    except Exception as e:
        log(f"深度思考搜索异常: {e}")

    # 列出底部所有可见文字按钮
    try:
        btns = await page.evaluate(r"""() => {
            const results = [];
            document.querySelectorAll('button, [role="button"], div[tabindex]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.top < 450 || rect.height < 10) return;
                const text = (el.innerText || '').trim();
                if (!text) return;
                results.push({
                    tag: el.tagName,
                    text: text.substring(0, 60),
                    className: (el.className || '').toString().substring(0, 200),
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    w: Math.round(rect.width), h: Math.round(rect.height),
                });
            });
            return results;
        }""")
        log(f"\n=== 底部可见文字按钮（{len(btns)}个）===")
        for b in btns:
            log(f"  [{b['tag']}] '{b['text']}' class='{b['className']}' "
                f"pos=({b['x']},{b['y']}) size={b['w']}x{b['h']}")
    except Exception as e:
        log(f"按钮列表异常: {e}")

    # 尝试点深度思考按钮并截图
    if think_info:
        log("\n=== 尝试点击深度思考相关按钮 ===")
        for t in think_info[:5]:
            text = t.get('text', '')
            if not text: continue
            log(f"尝试点击: {text} | class: {t['className'][:80]}")
            try:
                sel = f"text='{text}'"
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=3000)
                    log(f"  ✅ 点击成功")
                    await asyncio.sleep(1.5)
                    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_v3_after_click_1.png"))
                else:
                    log(f"  ⚠️ 未找到/不可见")
            except Exception as e:
                log(f"  ❌ 点击失败: {e}")

        await page.screenshot(path=str(Path(__file__).parent / "yuanbao_v3_after_click_final.png"))

    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_v3_final.png"))
    log(f"\n=== 调研完成 ===")


async def main():
    log("=== 元宝深度思考 UI 调研 v3 启动 ===")
    log(f"profile: {YUANBAO_PROFILE}")

    YUANBAO_PROFILE.mkdir(parents=True, exist_ok=True)

    existing_cookie = YUANBAO_PROFILE / "cookies.json"
    if existing_cookie.exists():
        log(f"发现已有 cookies.json ({existing_cookie.stat().st_size} bytes)")
    else:
        log("无 cookies.json，需要先手动登录")

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-service-autorun",
        "--window-size=1280,860",
    ]

    log("启动 Chromium（持久化 profile）...")
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=str(YUANBAO_PROFILE),
        headless=False,
        viewport={"width": 1280, "height": 860},
        args=launch_args,
        accept_downloads=True,
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    log("打开元宝...")
    await page.goto("https://yuanbao.tencent.com/chat/", wait_until="domcontentloaded", timeout=30000)

    # ===== 关键：给你够久时间登录 =====
    log("")
    log("=" * 60)
    log("请用微信/手机/QQ登录元宝。")
    log("我不动任何弹窗，不抢任何操作。")
    log("最多等 600 秒。你也可以直接告诉我「登好了」。")
    log("=" * 60)

    start = datetime.now()
    logged_in = False
    last_state = ""

    while True:
        elapsed = int((datetime.now() - start).total_seconds())
        # 每 60 秒汇报一次
        if elapsed % 60 == 0 and elapsed > 0:
            log(f"已等待 {elapsed}s | 当前状态: {last_state}")
            try:
                await page.screenshot(path=str(Path(__file__).parent / f"yuanbao_wait_{elapsed}s.png"))
                log(f"  截图: yuanbao_wait_{elapsed}s.png")
            except: pass

        if elapsed >= 600:
            log(f"\n⏰ 超时 600s，停止等待。可能仍未登录。")
            break

        result = await page.evaluate(r"""() => {
            return {
                nologin_count: document.querySelectorAll('[class*="nologin"]').length,
                body_text_sample: (document.body.innerText || '').substring(0, 200),
            };
        }""")
        last_state = f"nologin={result['nologin_count']}, text={result['body_text_sample'][:30]}"

        is_logged = await check_logged_in(page)
        if is_logged:
            log(f"\n✅ 检测到已登录！（等待 {elapsed}s）")
            logged_in = True
            break

        await asyncio.sleep(2)

    # 不管登没登录，都保存一份 cookies
    log("\n尝试保存 cookies...")
    await save_all_cookies(browser)

    # 如果已登录则调研 UI；未登录则只截图看看
    if logged_in:
        await research_after_login(page)
        # 再次保存 cookies
        log("\n=== 最终再次保存 cookies ===")
        await save_all_cookies(browser)
    else:
        log("未登录，跳过调研。")
        try:
            await page.screenshot(path=str(Path(__file__).parent / "yuanbao_not_logged.png"))
            log("截图: yuanbao_not_logged.png")
        except: pass

    log(f"\n[{datetime.now()}] 结束。浏览器保持打开 30 秒后关闭。")
    await asyncio.sleep(30)

    await browser.close()
    await pw.stop()
    log("浏览器已关闭")


if __name__ == "__main__":
    asyncio.run(main())
