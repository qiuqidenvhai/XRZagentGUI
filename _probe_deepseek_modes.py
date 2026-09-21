"""枚举 DeepSeek 网页当前真实的「模式/模型」选项。

连接后端已经在用的那个 Chromium（CDP），而不是另开一个持久化上下文
（另开会触发 SingletonLock 冲突）。
"""
import asyncio
import json
import os

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      r"D:\软件\XianRenZhangAgent\xrz_data\playwright_browsers")

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        # 连接已有 Chromium 的 CDP 端点
        try:
            browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print("CDP_CONNECT_FAIL", e)
            return

        ctxs = browser.contexts
        print("contexts:", len(ctxs))
        page = None
        for c in ctxs:
            for p in c.pages:
                u = p.url or ""
                print("  page:", u[:90])
                if "deepseek.com" in u:
                    page = p
        if page is None:
            print("NO_DEEPSEEK_PAGE")
            return

        await page.bring_to_front()
        info = await page.evaluate("""() => {
          const out = {};
          out.url = location.href;
          out.toggles = Array.from(document.querySelectorAll('.ds-toggle-button')).map(t => ({
            text: (t.textContent||'').trim(),
            selected: t.className.includes('selected'),
            pressed: t.getAttribute('aria-pressed')
          }));
          // radio-like mode pickers
          out.radios = Array.from(document.querySelectorAll("[role='radio']")).map(r => ({
            text: (r.textContent||'').trim().slice(0,24),
            checked: r.getAttribute('aria-checked'),
            cls: r.className
          }));
          // anything that looks like a model dropdown
          out.dropdowns = Array.from(document.querySelectorAll("[role='combobox'],[aria-haspopup],.ds-button--capsule"))
             .map(e => ({tag:e.tagName, text:(e.textContent||'').trim().slice(0,30), cls:(e.className||'').slice(0,60)}))
             .filter(e => e.text);
          return out;
        }""")
        print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
