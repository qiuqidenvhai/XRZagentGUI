"""
Qwen 平台调研 — 调研 chat.qwen.ai 的 DOM 结构
"""
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        print("=" * 60)
        print("调研 Qwen: https://chat.qwen.ai/")
        print("=" * 60)

        # 导航
        try:
            await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(8)  # 等待 SPA 渲染
        except Exception as e:
            print(f"[ERROR] 导航失败: {e}")
            await browser.close()
            return

        print(f"\n[页面标题] {await page.title()}")
        print(f"[URL] {page.url}")

        # 截图
        await page.screenshot(path="D:/软件/XianRenZhangAgent/agent_core/platform_research/screenshots/qwen_screenshot.png", full_page=False)
        print("[截图] 已保存 qwen_screenshot.png")

        # 提取 DOM 信息
        dom_info = await page.evaluate("""
        () => {
            const result = {};

            // === 输入框 ===
            result.input_boxes = [];

            // textarea
            document.querySelectorAll('textarea').forEach((el) => {
                const r = el.getBoundingClientRect();
                if (r.width > 30 && r.height > 10) {
                    result.input_boxes.push({
                        type: 'textarea',
                        className: el.className || '',
                        placeholder: el.placeholder || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        visible: r.width > 30,
                        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                    });
                }
            });

            // contenteditable
            document.querySelectorAll('[contenteditable]').forEach((el) => {
                const r = el.getBoundingClientRect();
                if (r.width > 30 && r.height > 10) {
                    result.input_boxes.push({
                        type: 'contenteditable',
                        className: el.className || '',
                        innerText: (el.innerText || '').substring(0, 50),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || '',
                        visible: true,
                        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                    });
                }
            });

            // === 发送按钮 ===
            result.send_buttons = [];
            document.querySelectorAll('button').forEach((el) => {
                const text = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                if (text.includes('send') || text.includes('send') || text.includes('送') || text.includes('submit')) {
                    const r = el.getBoundingClientRect();
                    result.send_buttons.push({
                        tag: el.tagName.toLowerCase(),
                        className: el.className || '',
                        innerText: (el.innerText || '').trim().substring(0, 30),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                    });
                }
            });

            // 也找含 arrow-right / paper-plane 图标的按钮
            document.querySelectorAll('button svg').forEach((svg) => {
                const btn = svg.closest('button');
                if (!btn) return;
                const cls = (btn.className || '').toString().toLowerCase();
                const al = (btn.getAttribute('aria-label') || '').toLowerCase();
                if (cls.includes('send') || al.includes('send') || cls.includes('arrow') || cls.includes('plane')) {
                    const r = btn.getBoundingClientRect();
                    result.send_buttons.push({
                        tag: 'button (icon)',
                        className: btn.className || '',
                        ariaLabel: al,
                        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
                    });
                }
            });

            // === 文件上传 ===
            result.file_upload = [];
            document.querySelectorAll('button, [role="button"]').forEach((el) => {
                const text = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.className || '')).toLowerCase();
                if (text.includes('upload') || text.includes('attach') || text.includes('paperclip') || text.includes('clip') || text.includes('上传') || text.includes('附件')) {
                    result.file_upload.push({
                        tag: el.tagName.toLowerCase(),
                        className: el.className || '',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        innerText: (el.innerText || '').trim().substring(0, 30),
                    });
                }
            });
            // 隐藏 file input
            document.querySelectorAll('input[type="file"]').forEach((el) => {
                result.file_upload.push({
                    type: 'hidden_file_input',
                    accept: el.getAttribute('accept') || '',
                    className: el.className || '',
                    hidden: !el.offsetWidth,
                });
            });

            // === 登录 ===
            result.login = { indicators: [] };
            document.querySelectorAll('a[href*="login"], a[href*="signin"]').forEach((a) => {
                result.login.indicators.push('Login link: ' + (a.href || ''));
            });
            document.querySelectorAll('.avatar, [class*="user-avatar"], [class*="profile"]').forEach((el) => {
                const img = el.querySelector('img');
                if (img) {
                    result.login.indicators.push('Has avatar: ' + (el.className || ''));
                }
            });

            // === 回复区域 ===
            result.message_containers = [];
            const msgSelectors = ['.message', '[class*="message"]', '[class*="chat"]', '[class*="conversation"]'];
            msgSelectors.forEach((sel) => {
                try {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0) {
                        result.message_containers.push({
                            selector: sel,
                            count: els.length,
                            lastText: (els[els.length - 1].innerText || '').substring(0, 100),
                        });
                    }
                } catch(e) {}
            });

            // === 页面 body class ===
            result.bodyClass = document.body ? (document.body.className || '') : '';

            return result;
        }
        """)

        # 打印结果
        print("\n" + "=" * 60)
        print("【输入框】")
        print("=" * 60)
        inputs = dom_info.get("input_boxes", [])
        if inputs:
            for i, inp in enumerate(inputs, 1):
                print(f"\n{i}. Type: {inp.get('type', '?')}")
                print(f"   className: {inp.get('className', '')}")
                print(f"   placeholder: {inp.get('placeholder', '')}")
                print(f"   ariaLabel: {inp.get('ariaLabel', '')}")
                print(f"   innerText: {inp.get('innerText', '')}")
                print(f"   rect: {inp.get('rect', {})}")
        else:
            print("(未找到输入框)")

        print("\n" + "=" * 60)
        print("【发送按钮】")
        print("=" * 60)
        buttons = dom_info.get("send_buttons", [])
        if buttons:
            for i, btn in enumerate(buttons, 1):
                print(f"\n{i}. tag: {btn.get('tag', '?')}")
                print(f"   className: {btn.get('className', '')}")
                print(f"   innerText: {btn.get('innerText', '')}")
                print(f"   ariaLabel: {btn.get('ariaLabel', '')}")
        else:
            print("(未找到发送按钮)")

        print("\n" + "=" * 60)
        print("【文件上传】")
        print("=" * 60)
        uploads = dom_info.get("file_upload", [])
        if uploads:
            for i, up in enumerate(uploads, 1):
                print(f"\n{i}. {json.dumps(up, ensure_ascii=False, indent=2)}")
        else:
            print("(未找到文件上传功能)")

        print("\n" + "=" * 60)
        print("【登录状态】")
        print("=" * 60)
        login_indicators = dom_info.get("login", {}).get("indicators", [])
        if login_indicators:
            for ind in login_indicators:
                print(f"  - {ind}")
        else:
            print("  (无明显登录指示)")

        print("\n" + "=" * 60)
        print("【消息容器】")
        print("=" * 60)
        msgs = dom_info.get("message_containers", [])
        if msgs:
            for m in msgs:
                print(f"  selector: {m['selector']}, count: {m['count']}")
                print(f"  last: {m['lastText'][:80]}")
        else:
            print("  (未找到消息容器)")

        print("\n" + "=" * 60)
        print("【Body Class】")
        print("=" * 60)
        print(f"  {dom_info.get('bodyClass', '(无)')}")

        # 保存 JSON 结果
        report_path = "D:/软件/XianRenZhangAgent/agent_core/platform_research/qwen_dom_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(dom_info, f, ensure_ascii=False, indent=2)
        print(f"\n[保存] DOM 报告已写入: {report_path}")

        await browser.close()
        print("\n[完成]")

if __name__ == "__main__":
    asyncio.run(main())
