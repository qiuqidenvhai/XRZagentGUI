"""
AI平台聊天界面DOM结构调研报告生成器
使用Playwright无头浏览器访问各大AI平台，截图并提取关键DOM元素信息
"""

import asyncio
import json
import os
from datetime import datetime
from playwright.async_api import async_playwright, Page

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = BASE_DIR  # 直接放在 platform_research/ 下
SCREENSHOTS_DIR = os.path.join(OUTPUT_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

PLATFORMS = [
    {"name": "deepseek", "display_name": "DeepSeek", "url": "https://chat.deepseek.com"},
    {"name": "qianwen", "display_name": "通义千问", "url": "https://tongyi.aliyun.com/qianwen/"},
    {"name": "doubao", "display_name": "豆包", "url": "https://www.doubao.com/chat/"},
    {"name": "yuanbao", "display_name": "元宝", "url": "https://yuanbao.tencent.com/chat/"},
    {"name": "chatgpt", "display_name": "ChatGPT", "url": "https://chatgpt.com/"},
    {"name": "gemini", "display_name": "Gemini", "url": "https://gemini.google.com/app"},
]


async def navigate_and_wait(page: Page, url: str, timeout=60000):
    """导航到页面并等待加载"""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as e:
        return f"navigate_error: {str(e)[:150]}"

    await asyncio.sleep(10)  # 等待SPA充分渲染
    return "ok"


def extract_dom_js() -> str:
    """返回要注入页面的JS代码字符串"""
    return r"""
    () => {
        const result = {};

        // ============ 1. 输入框 ============
        result.input_boxes = [];

        // textarea
        document.querySelectorAll('textarea').forEach((el, i) => {
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.height > 10) {
                result.input_boxes.push({
                    tag: 'textarea',
                    className: el.className.toString(),
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    role: el.getAttribute('role') || '',
                    parentClass: (el.parentElement && el.parentElement.className) || '',
                    visible: !(r.width === 0 && r.height === 0),
                });
            }
        });

        // [contenteditable]
        document.querySelectorAll('[contenteditable]').forEach((el, i) => {
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.height > 10) {
                result.input_boxes.push({
                    tag: '[contenteditable]',
                    className: el.className.toString(),
                    id: el.id || '',
                    innerText: (el.innerText || '').substring(0, 50),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    role: el.getAttribute('role') || '',
                    parentClass: (el.parentElement && el.parentElement.className) || '',
                    visible: true,
                });
            }
        });

        // div/span with role=textbox
        document.querySelectorAll('div[role="textbox"], span[role="textbox"]').forEach((el, i) => {
            const r = el.getBoundingClientRect();
            if (r.width > 50) {
                result.input_boxes.push({
                    tag: 'div[role=textbox]',
                    className: el.className.toString(),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    parentClass: (el.parentElement && el.parentElement.className) || '',
                    visible: !(r.width === 0),
                });
            }
        });

        // ============ 2. 发送按钮 ============
        result.send_buttons = [];
        const sendPatterns = ['send', 'submit', 'arrow', 'paper-plane', 'play', '送', '发送'];

        document.querySelectorAll('button, [role="button"]').forEach((el) => {
            const text = ((el.innerText || el.value || el.textContent || '') + ' ' +
                          (el.getAttribute('aria-label') || '') + ' ' +
                          (el.getAttribute('title') || '') + ' ' +
                          (el.className || '')).toLowerCase();
            if (sendPatterns.some(p => text.includes(p))) {
                const r = el.getBoundingClientRect();
                result.send_buttons.push({
                    tag: el.tagName.toLowerCase(),
                    className: el.className.toString(),
                    innerText: (el.innerText || '').trim().substring(0, 30),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    disabled: el.disabled,
                    parentClass: (el.parentElement && el.parentElement.className) || '',
                    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
                });
            }
        });

        // button > svg with send icon
        document.querySelectorAll('button svg').forEach((svgEl) => {
            const btn = svgEl.closest('button');
            if (!btn) return;
            const cls = (btn.className || '').toString().toLowerCase();
            const al = (btn.getAttribute('aria-label') || '').toLowerCase();
            if (cls.includes('send') || cls.includes('submit') || al.includes('send') || al.includes('submit')) {
                result.send_buttons.push({
                    tag: 'button > svg',
                    className: btn.className.toString(),
                    ariaLabel: btn.getAttribute('aria-label') || '',
                    parentClass: (btn.parentElement && btn.parentElement.className) || '',
                });
            }
        });

        // ============ 3. 文件上传 ============
        result.file_upload = [];
        const uploadPatterns = ['upload', 'attach', 'paperclip', 'clip', 'file', 'plus', '+', '上传', '附件'];

        document.querySelectorAll('button, [role="button"], a, .icon, svg').forEach((el) => {
            const text = ((el.innerText || el.textContent || '') + ' ' +
                          (el.getAttribute('aria-label') || '') + ' ' +
                          (el.getAttribute('title') || '') + ' ' +
                          (el.className || '')).toLowerCase();
            if (uploadPatterns.some(p => text.includes(p))) {
                result.file_upload.push({
                    tag: el.tagName.toLowerCase(),
                    className: el.className.toString(),
                    innerText: (el.innerText || '').trim().substring(0, 30),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    parentClass: (el.parentElement && el.parentElement.className) || '',
                });
            }
        });

        // 隐藏的文件input
        result.file_inputs = [];
        document.querySelectorAll('input[type="file"]').forEach((el) => {
            result.file_inputs.push({
                accept: el.getAttribute('accept') || '',
                multiple: el.multiple,
                name: el.name || '',
                className: el.className.toString(),
            });
        });

        // ============ 4. 登录状态 ============
        result.login_status = { detected: false, indicators: [] };

        const avatarClsPatterns = ['avatar', 'profile', 'user-icon', 'user-avatar', 'account'];
        document.querySelectorAll('*').forEach(el => {
            const cls = (el.className || '').toString().toLowerCase();
            if (avatarClsPatterns.some(p => cls.includes(p))) {
                const img = el.querySelector('img');
                if (img) {
                    result.login_status.detected = true;
                    result.login_status.indicators.push('Avatar img: <' + el.tagName + '> .' + cls.split(/\s+/)[0]);
                }
            }
        });

        // 检测登录/注册链接
        document.querySelectorAll('a[href*="login"], a[href*="signin"], a[href*="sign-in"]').forEach(a => {
            result.login_status.indicators.push('Login link: ' + (a.href || ''));
        });

        // 检测欢迎语中的已登录用户名
        document.querySelectorAll('[class*="username"], [class*="user"], [class*="account"]').forEach(el => {
            const t = (el.innerText || '').trim();
            if (t.length > 1 && t.length < 20 && /^[^\s]+$/.test(t)) {
                result.login_status.indicators.push('Possible username: "' + t + '" in <' + el.tagName + '>');
            }
        });

        // ============ 5. iframe 检测 ============
        result.iframe_info = [];
        document.querySelectorAll('iframe').forEach((f) => {
            result.iframe_info.push({
                src: f.src || '',
                className: f.className.toString(),
            });
        });

        // ============ 6. 页面基本元信息 ============
        result.page_info = {
            title: document.title,
            url: window.location.href,
            width: document.documentElement.clientWidth,
            height: document.documentElement.clientHeight,
            bodyClassName: document.body ? (document.body.className || '') : '',
        };

        // ============ 7. 整体body HTML预览 (前2000字符) ============
        result.body_html_preview = document.body ? document.body.innerHTML.substring(0, 2000) : '';

        return result;
    }
    """


async def extract_dom_with_playwright(page: Page):
    """使用 Playwright 的 $ 方法更高效地提取元素"""
    dom_info = await page.evaluate(extract_dom_js())

    # 也尝试用 Playwright 自己的选择器来查找
    pw_findings = {"pw_textareas": [], "pw_contenteditables": [], "pw_buttons": []}

    for sel, key in [("textarea", "pw_textareas"), ("[contenteditable]", "pw_contenteditables")]:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                rect = await el.bounding_box()
                if rect and rect["width"] > 50:
                    pw_findings[key].append({
                        "selector": sel,
                        "className": await el.get_attribute("class") or "",
                        "boundingBox": rect,
                    })
        except:
            pass

    # 查找所有按钮，分析哪些可能是发送按钮
    try:
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            text = (await btn.inner_text() or "").lower()
            cls = (await btn.get_attribute("class") or "").lower()
            al = (await btn.get_attribute("aria-label") or "").lower()
            combined = text + " " + cls + " " + al
            send_kw = ["send", "submit", "arrow-right", "paper-plane", "send-icon", "发送"]
            if any(kw in combined for kw in send_kw):
                rect = await btn.bounding_box()
                pw_findings["pw_buttons"].append({
                    "textContent": text[:30],
                    "className": cls[:100],
                    "rect": rect,
                })
    except:
        pass

    dom_info["playwright_findings"] = pw_findings
    return dom_info


async def process_platform(platform_config):
    """处理单个平台"""
    name = platform_config["display_name"]
    url = platform_config["url"]
    print(f"\n{'='*60}")
    print(f"正在调研: {name}")
    print(f"URL: {url}")
    print(f"{'='*60}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page = await context.new_page()

        # 1. Navigate
        status = await navigate_and_wait(page, url)

        # 2. Screenshot
        screenshot_filename = f"{platform_config['name']}_screenshot.png"
        screenshot_path = os.path.join(SCREENSHOTS_DIR, screenshot_filename)
        try:
            await page.screenshot(path=screenshot_path, full_page=False)
            print(f"  截图保存: {screenshot_path}")
        except Exception as e:
            print(f"  截图失败: {e}")
            screenshot_path = None

        # 3. Extract DOM
        dom_info = await extract_dom_with_playwright(page)
        print(f"  DOM提取完成 - page_info: {dom_info.get('page_info', {})}")
        print(f"  输入框: {len(dom_info.get('input_boxes', []))}, 发送按钮: {len(dom_info.get('send_buttons', []))}, 上传: {len(dom_info.get('file_upload', []))}")

        await browser.close()

    report = {
        "platform": name,
        "url": url,
        "timestamp": datetime.now().isoformat(),
        "load_status": status,
        "screenshot": screenshot_path,
    }
    report.update(dom_info)

    return report


async def generate_markdown_report(all_reports):
    """生成Markdown格式报告"""
    md_path = os.path.join(OUTPUT_DIR, "platform_research_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# AI平台聊天界面DOM结构调研报告\n\n")
        f.write(f"> 调研时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 共调研 {len(PLATFORMS)} 个平台\n\n")

        for report in all_reports:
            name = report.get("platform", "?")
            url = report.get("url", "")
            status = report.get("load_status", "?")
            screenshot = report.get("screenshot")
            page_info = report.get("page_info", {})
            input_boxes = report.get("input_boxes", [])
            send_buttons = report.get("send_buttons", [])
            file_uploads = report.get("file_upload", [])
            file_inputs = report.get("file_inputs", [])
            login = report.get("login_status", {})
            iframes = report.get("iframe_info", [])
            pw_findings = report.get("playwright_findings", {})
            body_html = report.get("body_html_preview", "")

            f.write(f"---\n\n## {name}\n\n")
            f.write(f"- **URL**: `{url}`\n")
            f.write(f"- **加载状态**: {status}\n")
            if screenshot:
                f.write(f"- **截图**: [{os.path.basename(screenshot)}](screenshots/{os.path.basename(screenshot)})\n")
            f.write(f"- **body class**: `{page_info.get('bodyClassName', '')}`\n")
            f.write(f"- **页面标题**: `{page_info.get('title', '')}`\n")
            f.write(f"- **当前URL**: `{page_info.get('url', '')}`\n")
            f.write(f"- **视口**: {page_info.get('width', 0)}x{page_info.get('height', 0)}\n")

            # iframe
            if iframes:
                f.write(f"\n### iframe\n\n")
                for iframe in iframes[:5]:
                    f.write(f"- src: `{iframe.get('src', '')}`\n")

            # 输入框
            f.write(f"\n### 聊天输入框 ({len(input_boxes)} 个)\n\n")
            if input_boxes:
                f.write(f"| # | 标签 | Class | Placeholder | aria-label | 可见 |\n")
                f.write(f"|---|------|-------|-------------|------------|-----|\n")
                for i, box in enumerate(input_boxes[:10], 1):
                    cls_short = str(box.get("className", ""))[:60]
                    pl = (box.get("placeholder", "") or "")[:40]
                    al = (box.get("ariaLabel", "") or "")[:30]
                    vis = "yes" if box.get("visible", True) else "?"
                    f.write(f"| {i} | `{box.get('tag','')}` | `{cls_short}` | {pl} | {al} | {vis} |\n")
            else:
                f.write("**未找到标准输入框**\n\n")

            # Playwright额外发现
            if pw_findings.get("pw_textareas"):
                f.write(f"\n> Playwright额外发现 {len(pw_findings['pw_textareas'])} 个 textarea\n\n")
                for idx, tw in enumerate(pw_findings["pw_textareas"][:5], 1):
                    f.write(f"  {idx}. className=`{str(tw.get('className',''))[:50]}` rect={tw.get('boundingBox',{})}\n")

            if pw_findings.get("pw_contenteditables"):
                f.write(f"\n> Playwright额外发现 {len(pw_findings['pw_contenteditables'])} 个 contenteditable\n\n")
                for idx, ce in enumerate(pw_findings["pw_contenteditables"][:5], 1):
                    f.write(f"  {idx}. className=`{str(ce.get('className',''))[:50]}` rect={ce.get('boundingBox',{})}\n")

            if pw_findings.get("pw_buttons"):
                f.write(f"\n> Playwright发现 {len(pw_findings['pw_buttons'])} 个疑似发送按钮\n\n")
                for idx, btn in enumerate(pw_findings["pw_buttons"][:5], 1):
                    f.write(f"  {idx}. text=`{btn.get('textContent','')}` cls=`{btn.get('className','')[:50]}`\n")

            # 发送按钮
            f.write(f"\n### 发送按钮 ({len(send_buttons)} 个)\n\n")
            if send_buttons:
                f.write(f"| # | 标签 | Class | 文字 | aria-label |\n")
                f.write(f"|---|------|-------|------|------------|\n")
                for i, btn in enumerate(send_buttons[:10], 1):
                    cls_short = str(btn.get("className", ""))[:50]
                    f.write(f"| {i} | `{btn.get('tag','')}` | `{cls_short}` | {(btn.get('innerText','') or '')[:30]} | {(btn.get('ariaLabel','') or '')[:30]} |\n")
            else:
                f.write("**未找到明显发送按钮**\n\n")

            # 文件上传
            f.write(f"\n### 文件上传 ({len(file_uploads)} 个按钮, {len(file_inputs)} 个 input)\n\n")
            if file_uploads:
                f.write(f"| # | 标签 | aria-label | 文字 |\n")
                f.write(f"|---|------|------------|------|\n")
                for i, fu in enumerate(file_uploads[:10], 1):
                    f.write(f"| {i} | `{fu.get('tag','')}` | {(fu.get('ariaLabel','') or '')[:30]} | {(fu.get('innerText','') or '')[:30]} |\n")
            if file_inputs:
                for fi in file_inputs:
                    f.write(f"- `<input type=\"file\">` accept={fi.get('accept','')} multiple={fi.get('multiple',False)}\n")
            if not file_uploads and not file_inputs:
                f.write("**未找到文件上传功能**\n")

            # 登录状态
            f.write(f"\n### 登录状态\n\n")
            indicators = login.get("indicators", [])
            if login.get("detected"):
                f.write(f"- **检测到登录态**\n")
            if indicators:
                f.write(f"- 检测详情:\n")
                for ind in indicators[:10]:
                    f.write(f"  - {ind}\n")
            else:
                f.write(f"- 无明确检测结果\n")

            # body html snippet
            if body_html and "<div" in body_html:
                f.write(f"\n> **Body HTML前500字符**: `{body_html[:500]}`\n\n")

        # 总结
        f.write(f"\n---\n\n# 总结对比\n\n")
        f.write("| 平台 | 状态 | 输入框数 | 发送按钮 | 上传 | body class摘要 |\n")
        f.write("|------|------|---------|---------|------|----------------|\n")
        for r in all_reports:
            name = r.get("platform", "?")
            status = str(r.get("load_status", "?"))[:40]
            ib = len(r.get("input_boxes", []))
            sb = len(r.get("send_buttons", []))
            fu = len(r.get("file_upload", []))
            bcl = str(r.get("page_info", {}).get("bodyClassName", ""))[:40]
            f.write(f"| {name} | {status} | {ib} | {sb} | {fu} | `{bcl}` |\n")

    return md_path


async def generate_json_report(all_reports):
    """保存JSON格式报告"""
    json_path = os.path.join(OUTPUT_DIR, "platform_research_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, ensure_ascii=False, indent=2)
    return json_path


async def main():
    """主函数"""
    print("="*60)
    print("AI平台聊天界面DOM结构调研")
    print("开始时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("="*60)

    all_reports = []

    for platform in PLATFORMS:
        try:
            report = await process_platform(platform)
            all_reports.append(report)
            print(f"  OK: {report['platform']} - 输入框={len(report.get('input_boxes',[]))}, 发送={len(report.get('send_buttons',[]))}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n  ERROR 调研 {platform['display_name']}: {e}")
            all_reports.append({
                "platform": platform["display_name"],
                "url": platform["url"],
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            })

    # 保存报告
    json_path = await generate_json_report(all_reports)
    md_path = await generate_markdown_report(all_reports)

    print(f"\n{'='*60}")
    print("报告已保存:")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print(f"  Screenshots: {SCREENSHOTS_DIR}/")
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())
