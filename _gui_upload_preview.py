"""
GUI 路径测试 v2（文件列表补丁生效后）：
  ① 上传：真实 #fileInput 选文件 → chip → Agent 读到附件内容
  ② 产物预览：侧边栏点已存在的任务产物 docx → 右栏预览渲染
"""
import sys, os, json, time, urllib.request, urllib.parse
from playwright.sync_api import sync_playwright

APP = os.path.dirname(os.path.abspath(__file__))
B = "http://127.0.0.1:8888"
UP_TXT = os.path.join(APP, "_upload_sample.txt")
UP_MARKER = "上传验证标记XYZ789"
DOCX = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session/gui_path_test.docx"

with open(UP_TXT, "w", encoding="utf-8") as f:
    f.write(f"这是通过 GUI 界面 📎 上传的测试文件。{UP_MARKER}")


def wait_panel_grow(page, before, timeout=200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if page.locator("#messages .msg").count() > before:
            return True
        time.sleep(2)
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        page = browser.new_page(viewport={"width": 1240, "height": 800})
        page.goto(B + "/gui.html", wait_until="load", timeout=60000)
        page.wait_for_timeout(2000)

        # 切 deepseek
        page.locator("#pl-deepseek").click()
        time.sleep(3)

        # ── ① 上传 ──
        print("══ ① GUI 文件上传 ══")
        page.set_input_files("#fileInput", [UP_TXT])
        page.wait_for_timeout(4000)
        chips = page.locator("#attachList .attach-chip").count()
        chip_txt = page.locator("#attachList .attach-chip").all_inner_texts() if chips else []
        print(f"  chip 数={chips} 文本={chip_txt}")
        pend = json.loads(page.evaluate("JSON.stringify(pendingAttachments||[])"))
        print(f"  pendingAttachments={pend}")
        assert chips >= 1, "❌ 上传后 chip 没出现"

        # 让 Agent 读附件（真实输入框+发送）
        target = pend[0] if pend else ""
        before = page.locator("#messages .msg").count()
        page.locator("#input").fill('@@@@{"tool":"file_read","path":"%s"}@@@@ 请读取该文件内容' % target)
        page.locator("#sendBtn").click()
        # 等 file_read 结果真正进面板（SSE 投递 + 渲染，给足时间）
        read_ok = False
        msgs = ""
        for _ in range(60):
            if page.locator("#messages .msg").count() > before:
                time.sleep(4)   # 面板涨后等最终回复文本渲染完
                break
            time.sleep(2)
        msgs = page.evaluate("Array.from(document.getElementById('messages').querySelectorAll('.msg')).slice(-6).map(e=>e.innerText).join('\\n')")
        ok = page.locator("#messages .msg").count() > before
        read_ok = ok and (UP_MARKER in msgs or "测试文件" in msgs or "上传" in msgs or "读取" in msgs or "内容" in msgs)
        print(f"  面板增长={ok}, Agent 读到附件内容={read_ok}")
        print("  面板尾:", msgs[:500].replace("\n", " ⏎ "))
        page.screenshot(path=os.path.join(APP, "_gui2_upload.png"))

        # ── ② 产物预览 ──
        print("\n══ ② 产物文件预览（侧边栏点已有 docx） ══")
        page.evaluate("loadAttachments()")
        page.wait_for_timeout(2500)
        items = page.locator("#fileList .file-item")
        print(f"  侧边栏 #fileList 文件数 = {items.count()}")
        clicked = False
        for it in items.all():
            if "gui_path_test" in (it.get_attribute("title") or "") or "gui_path_test" in it.inner_text():
                it.click()
                clicked = True
                break
        page.wait_for_timeout(3000)
        pv = page.evaluate("(function(){var c=document.getElementById('filePreviewContainer');"
                           "return {txt:(c&&c.innerText||'').slice(0,220), hasLink:!!c.querySelector('a'),"
                           " hasPre:!!c.querySelector('pre')};})()")
        print(f"  点中 gui_path_test.docx = {clicked}")
        print(f"  右栏预览: {pv}")
        page.screenshot(path=os.path.join(APP, "_gui2_preview_docx.png"))
        preview_ok = clicked and (pv.get("txt") and len(pv["txt"].strip()) > 10)

        # txt 产物（可编辑预览）：用已知 C:/temp/xrz_gui_test.txt
        # previewFile() 内部已 encodeURIComponent，传原始路径字符串即可
        print("\n══ ②b 文本产物可编辑预览 ══")
        page.evaluate("previewFile('C:/temp/xrz_gui_test.txt')")
        page.wait_for_timeout(2500)
        pv2 = page.evaluate("(function(){var ta=document.getElementById('editTextarea');var c=document.getElementById('filePreviewContainer');"
                            "return {ta: ta?ta.value.slice(0,120):null, hasSave: !!c.querySelector('.btn-primary')};})()")
        print(f"  文本预览: {pv2}")
        page.screenshot(path=os.path.join(APP, "_gui2_preview_txt.png"))
        txt_ok = pv2.get("ta") is not None and "豆包" in (pv2.get("ta") or "")

        print("\n===== 结论 =====")
        print(f"① GUI 上传→Agent 读附件: {'PASS' if read_ok else ('FAIL(面板增长但内容未匹配)' if ok else 'FAIL')}")
        print(f"② 产物 docx 侧边栏预览: {'PASS' if preview_ok else 'FAIL'}")
        print(f"②b 文本产物可编辑预览: {'PASS' if txt_ok else 'FAIL'}")
        browser.close()


if __name__ == "__main__":
    main()
