#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""元宝深度思考 UI 真机调研（v2）
用户说元宝登录过一次，用持久化 profile 启动，抓取底部工具栏 + 深度思考按钮。
"""
import asyncio
import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 确保 import 路径
sys.path.insert(0, str(Path(__file__).parent))

# D 盘数据
DATA_ROOT = Path(r"D:\软件\XianRenZhangAgent\xrz_data")
BROWSER_DATA_ROOT = DATA_ROOT / ".xianrenzhang_agent" / "browser_profiles"
YUANBAO_PROFILE = BROWSER_DATA_ROOT / "yuanbao"

# 日志文件
LOG_FILE = Path(__file__).parent / "research_yuanbao_v2.log"

class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

async def main():
    log_f = open(LOG_FILE, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_f)
    sys.stderr = Tee(sys.__stderr__, log_f)

    print(f"[{datetime.now()}] 元宝深度思考 UI 调研启动")
    print(f"profile 目录: {YUANBAO_PROFILE}")

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()

    YUANBAO_PROFILE.mkdir(parents=True, exist_ok=True)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-service-autorun",
        "--window-size=1280,860",
    ]

    print("启动 Chromium（持久化 profile）...")
    browser = await pw.chromium.launch_persistent_context(
        user_data_dir=str(YUANBAO_PROFILE),
        headless=False,
        viewport={"width": 1280, "height": 860},
        args=launch_args,
        accept_downloads=True,
    )
    page = browser.pages[0] if browser.pages else await browser.new_page()

    print("goto 元宝 chat...")
    await page.goto("https://yuanbao.tencent.com/chat/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)

    # 检测登录弹窗
    print("\n=== 检测登录弹窗 ===")
    is_yuanbao_login = False
    try:
        login_modal_info = await page.evaluate("""() => {
            const containers = document.querySelectorAll(
                '.ant-modal-wrap, .ant-modal, [class*="login"], [class*="Login"], ' +
                '[class*="qr"], [class*="QR"], [class*="scan"], [class*="Scan"], ' +
                '[class*="nologin"], [class*="no-login"], [class*="wechat"], [class*="weixin"]'
            );
            const results = [];
            for (const c of containers) {
                if (c.offsetWidth === 0 && c.offsetHeight === 0) continue;
                const html = (c.innerHTML || '').substring(0, 300).toLowerCase();
                results.push({
                    tag: c.tagName,
                    className: c.className.substring(0, 150),
                    textPreview: html,
                    visible: true,
                });
            }
            return results;
        }""")
        print(f"登录弹窗检测: {json.dumps(login_modal_info, ensure_ascii=False, indent=2)}")
        for m in login_modal_info:
            if ('nologin' in m.get('className', '').lower() or
                'login' in m.get('className', '').lower() or
                '微信登录' in m.get('textPreview', '') or
                '扫码' in m.get('textPreview', '') or
                '登录' in m.get('textPreview', '')):
                is_yuanbao_login = True
    except Exception as e:
        print(f"登录弹窗检测异常: {e}")

    # 截图1：初始状态
    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_initial.png"))
    print("截图: yuanbao_initial.png")

    # 综合判断未登录：nologin class / 输入框提示 / 登录弹窗 / body 文字
    try:
        body_text = await page.evaluate("""() => document.body.innerText || '' """)
        if ('请登录' in body_text or '未登录' in body_text or
            '微信登录' in body_text or '扫码' in body_text or
            '请使用微信' in body_text):
            is_yuanbao_login = True
        print(f"body 登录关键词命中: {is_yuanbao_login}")
    except Exception as e:
        print(f"body 文字检测异常: {e}")

    # 等待用户登录
    if is_yuanbao_login:
        print("\n⚠️ 元宝未登录，请扫码登录（微信/手机/QQ）")
        print("请在浏览器窗口中完成登录，脚本会自动检测并继续...")
        print("等待最多 300 秒...")
        for i in range(300):
            await asyncio.sleep(1)
            if i % 10 == 0:
                print(f"  已等待 {i}s，请登录...")
            # 检测是否已登录：nologin class 消失、出现输入框、登录弹窗消失
            try:
                still_nologin = await page.evaluate("""() => {
                    return document.querySelectorAll('[class*="nologin"]').length > 0;
                }""")
                has_input = await page.evaluate("""() => {
                    const input = document.querySelector('[contenteditable].ql-editor, textarea');
                    return input && input.offsetWidth > 0;
                }""")
                login_text = await page.evaluate("""() => {
                    const text = document.body.innerText || '';
                    return text.includes('请登录') || text.includes('微信登录') || text.includes('未登录');
                }""")
                if not still_nologin and has_input and not login_text:
                    print(f"  ✅ 检测到已登录（等待 {i+1}s）")
                    break
            except Exception:
                pass
        else:
            print("等待 300 秒后仍未登录，继续抓取当前状态...")
    else:
        print("未检测到登录弹窗，继续抓取...")

    await asyncio.sleep(3)

    # 截图2：登录后状态
    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_after_login.png"))
    print("截图: yuanbao_after_login.png")

    # 抓取底部工具栏所有按钮
    print("\n=== 底部工具栏按钮 ===")
    try:
        buttons_info = await page.evaluate("""() => {
            // 找所有可点击元素
            const clickables = document.querySelectorAll('button, [role="button"], [class*="button"], [class*="Button"], div[tabindex]');
            const results = [];
            for (const b of clickables) {
                if (b.offsetWidth === 0 || b.offsetHeight === 0) continue;
                const rect = b.getBoundingClientRect();
                // 只看页面下半部分（工具栏通常在底部）
                if (rect.top < 400) continue;
                const text = (b.innerText || '').trim();
                const cls = (b.className || '').toString().substring(0, 120);
                results.push({
                    tag: b.tagName,
                    text: text.substring(0, 50),
                    className: cls,
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    w: Math.round(rect.width),
                    h: Math.round(rect.height),
                });
            }
            return results;
        }""")
        print(f"底部按钮数量: {len(buttons_info)}")
        for b in buttons_info:
            print(f"  [{b['tag']}] text='{b['text']}' class='{b['className']}' pos=({b['x']},{b['y']}) size={b['w']}x{b['h']}")
    except Exception as e:
        print(f"按钮抓取异常: {e}")

    # 专门找深度思考相关
    print("\n=== 深度思考相关元素 ===")
    try:
        think_info = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            const results = [];
            for (const el of all) {
                if (el.offsetWidth === 0) continue;
                const text = (el.innerText || '').trim();
                const cls = (el.className || '').toString();
                if (text.includes('深度思考') || text.includes('深思') || text.includes('深度') ||
                    text.includes('思考') || text.includes('推理') || text.includes('reasoning') ||
                    text.includes('think') || text.includes('Think') ||
                    cls.includes('think') || cls.includes('Think') || cls.includes('reason') ||
                    cls.includes('deep') || cls.includes('Deep')) {
                    if (text.length > 50) continue;  // 跳过大块文本
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 30),
                        className: cls.substring(0, 150),
                        id: el.id || '',
                    });
                    if (results.length > 30) break;
                }
            }
            return results;
        }""")
        print(f"深度思考相关元素: {len(think_info)}")
        for t in think_info:
            print(f"  [{t['tag']}] text='{t['text']}' class='{t['className']}' id='{t['id']}'")
    except Exception as e:
        print(f"深度思考搜索异常: {e}")

    # 抓取输入框附近的完整 HTML（底部工具栏区域）
    print("\n=== 输入框附近 HTML ===")
    try:
        # 找输入框
        editor = page.locator("[contenteditable].ql-editor, textarea, [class*='input']")
        if await editor.count() > 0:
            # 抓输入框的父容器 HTML
            parent_html = await page.evaluate("""() => {
                const editor = document.querySelector('[contenteditable].ql-editor, textarea, [class*=input]');
                if (!editor) return 'NO_EDITOR';
                let p = editor;
                // 向上找3层父容器
                for (let i = 0; i < 4; i++) {
                    if (p.parentElement) p = p.parentElement;
                }
                return p.outerHTML.substring(0, 3000);
            }""")
            print(f"输入框父容器 HTML（前3000字符）:\n{parent_html}")
        else:
            print("未找到输入框")
    except Exception as e:
        print(f"输入框 HTML 抓取异常: {e}")

    # 抓取 ant-select 下拉框（类似 Qwen 的模式选择）
    print("\n=== ant-select / 下拉框 ===")
    try:
        select_info = await page.evaluate("""() => {
            const sels = document.querySelectorAll('.ant-select, .ant-select-selector, [class*="select"], [class*="Select"], [class*="dropdown"], [class*="Dropdown"]');
            const results = [];
            for (const s of sels) {
                if (s.offsetWidth === 0) continue;
                const text = (s.innerText || '').trim();
                const cls = (s.className || '').toString();
                if (text.length > 100) continue;
                results.push({
                    tag: s.tagName,
                    text: text.substring(0, 50),
                    className: cls.substring(0, 150),
                });
                if (results.length > 20) break;
            }
            return results;
        }""")
        print(f"下拉框元素: {len(select_info)}")
        for s in select_info:
            print(f"  [{s['tag']}] text='{s['text']}' class='{s['className']}'")
    except Exception as e:
        print(f"下拉框搜索异常: {e}")

    # 最终截图
    await page.screenshot(path=str(Path(__file__).parent / "yuanbao_final.png"))
    print("\n截图: yuanbao_final.png")
    print(f"\n[{datetime.now()}] 调研完成")

    # 不自动关浏览器，让用户看
    print("浏览器保持打开，10秒后自动关闭...")
    await asyncio.sleep(10)
    await browser.close()
    await pw.stop()
    log_f.close()

if __name__ == "__main__":
    asyncio.run(main())
