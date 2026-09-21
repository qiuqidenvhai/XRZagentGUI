"""验证 API 地址修复：file:// 直接打开 与 http:// 后端打开 两种方式"""
import asyncio
from playwright.async_api import async_playwright

GUI_FILE = 'D:/软件/XianRenZhangAgent/gui.html'
SRC = r'D:\workbuddy工作区\2026-08-11-21-39-15\test_pdf_sample.pdf'


async def probe(page, label, url):
    print(f"\n{'='*60}\n[方式] {label}\n  URL: {url}\n{'='*60}")
    errs = []
    page.on('pageerror', lambda e: errs.append(str(e)))
    await page.goto(url)
    await page.wait_for_timeout(2000)

    api = await page.evaluate("() => (typeof API!=='undefined') ? API : '<<undefined>>'")
    print(f"  API 地址 = {api}")

    if api.startswith('file:'):
        print("  ✗ API 仍是 file:// —— 修复未生效")
        return False

    # 健康探测
    health = await page.evaluate("""async () => {
        try { const r = await fetch(API + '/health'); return {ok:true, s:r.status}; }
        catch(e){ return {ok:false, err:String(e)}; }
    }""")
    print(f"  /health → {health}")

    # 真实上传
    inp = await page.query_selector('#fileInput')
    if not inp:
        print("  ✗ 未找到 #fileInput")
        return False
    await inp.set_input_files(SRC)
    ok = False
    for i in range(24):
        await page.wait_for_timeout(500)
        n = await page.evaluate(
            "() => (typeof pendingAttachments!=='undefined')?pendingAttachments.length:-1")
        if n > 0:
            print(f"  ✓ 上传成功：约{(i+1)*0.5:.1f}s 后 pendingAttachments={n}")
            ok = True
            break
    if not ok:
        n = await page.evaluate(
            "() => (typeof pendingAttachments!=='undefined')?pendingAttachments.length:-1")
        print(f"  ✗ 上传失败：12s 后 pendingAttachments={n}")

    # 附件是否渲染到界面（用户能看见）
    shown = await page.evaluate("""() => {
        const el = document.getElementById('attachList');
        if(!el) return 'no #attachList';
        return {display: el.style.display, children: el.children.length,
                text: (el.innerText||'').slice(0,80)};
    }""")
    print(f"  界面附件区: {shown}")
    if errs:
        print("  页面错误:", errs[:3])
    return ok


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={'width': 1400, 'height': 900})

        r1 = await probe(await ctx.new_page(), 'A. 双击 html（file 协议）',
                         'file:///' + GUI_FILE.replace('\\', '/'))
        r2 = await probe(await ctx.new_page(), 'B. 后端服务打开（真实用户方式）',
                         'http://127.0.0.1:8888/')

        print(f"\n{'='*60}\n结论: file协议={'PASS' if r1 else 'FAIL'}, "
              f"http协议={'PASS' if r2 else 'FAIL'}\n{'='*60}")
        await b.close()


asyncio.run(main())
