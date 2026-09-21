"""Live DOM audit of chat.deepseek.com against our platforms.json config.

Checks what model/mode controls ACTUALLY exist on the page right now, so the
config can't drift away from reality again.
"""
import asyncio
import json
import os
import sys

os.environ.setdefault("XRZ_NO_GUI", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PROFILE = os.path.join(HERE, "xrz_data", ".xianrenzhang_agent", "browser_profiles", "deepseek")
BROWSERS = os.path.join(HERE, "xrz_data", "playwright_browsers")

JS_AUDIT = r"""
(() => {
  const out = {};
  const vis = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  // 1) toggle buttons (深度思考 / 智能搜索)
  out.toggles = [...document.querySelectorAll('div.ds-toggle-button, [class*="ds-toggle-button"]')]
    .filter(vis)
    .map(e => ({
      text: (e.innerText || '').trim().slice(0, 40),
      selected: e.className.includes('ds-toggle-button--selected'),
      ariaPressed: e.getAttribute('aria-pressed'),
    }));

  // 2) any model/mode picker at all
  const sel = 'button[aria-haspopup], [role="combobox"], [role="listbox"], [role="radiogroup"], [role="menu"], [role="menuitem"], [role="radio"]';
  out.pickers = [...document.querySelectorAll(sel)].filter(vis).map(e => ({
    tag: e.tagName,
    role: e.getAttribute('role'),
    haspopup: e.getAttribute('aria-haspopup'),
    text: (e.innerText || '').trim().slice(0, 40),
  }));

  // 3) legacy fake mode labels must be absent
  const body = document.body.innerText || '';
  out.legacy = {
    快速模式: body.includes('快速模式'),
    专家模式: body.includes('专家模式'),
    识图模式: body.includes('识图模式'),
    深度思考: body.includes('深度思考'),
    智能搜索: body.includes('智能搜索'),
  };

  // 4) composer present?
  const ta = document.querySelector('textarea');
  out.composer = ta ? { placeholder: ta.getAttribute('placeholder') || '', visible: vis(ta) } : null;
  out.url = location.href;
  out.title = document.title;
  return JSON.stringify(out);
})()
"""


async def main():
    from playwright.async_api import async_playwright

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE,
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://chat.deepseek.com", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        raw = await page.evaluate(JS_AUDIT)
        print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
