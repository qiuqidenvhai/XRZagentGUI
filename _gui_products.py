"""
布置「会产生真实文件产物」的任务并逐一验收（走 GUI 输入框，不直打 API）：
  ① Word (docx_create)  ② PPT (pptx_create)  ③ PDF (pdf_create)  ④ 代码 (file_write)
产物落到 XianRenZhang_tasks/gui_session/ → 被 /files 补丁列进侧边栏 → 点击可预览。

验收标准（每类）：
  A. 文件真实落盘且格式正确（docx/pptx=zip, pdf=%PDF, py=文本）
  B. 出现在 GUI 侧边栏 #fileList
  C. 点击后右侧预览渲染出内容
"""
import os, json, time, zlib
from playwright.sync_api import sync_playwright

APP = os.path.dirname(os.path.abspath(__file__))
B = "http://127.0.0.1:8888"
SESS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRelZhang_tasks".replace("Rel","Ren")  # gui_session 目录
SESS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session"

def proto(tool, fname, content):
    p = SESS.replace("\\", "/") + "/" + fname
    return "@@@@%s@@@@ 请执行生成" % json.dumps(
        {"tool": tool, "path": p, "content": content}, ensure_ascii=False)

PRODS = [
    ("word", "docx_create", "prod_word.docx", "docx",
     "# 产品验收报告\n## Word 产物\n正文：Word产物验证成功，多段落生成。"),
    ("ppt",  "pptx_create", "prod_ppt.pptx",  "pptx",
     "# 第1页 PPT产物验证成功\n- 要点A\n- 要点B\n# 第2页 数据\n- X\n- Y\n# 第3页 谢谢"),
    ("pdf",  "pdf_create",   "prod_pdf.pdf",   "pdf",
     "# PDF 产物验证\n## 章节一\nPDF产物验证成功，reportlab 生成。\n- 要点一\n- 要点二"),
    ("code", "file_write",   "prod_code.py",   "py",
     "# prod_code.py\nprint('prod_code 验证成功')\ndef add(a,b):\n    return a+b"),
]


def valid(ext, data):
    if ext in ("docx", "pptx", "xlsx"):
        return data[:2] == b"PK"
    if ext == "pdf":
        return data[:4] == b"%PDF"
    return len(data) > 0


def main():
    for tag, tool, fname, ext, content in PRODS:
        p = os.path.join(SESS, fname)
        try:
            os.remove(p)
        except Exception:
            pass
    os.makedirs(SESS, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                    args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        page = browser.new_page(viewport={"width": 1240, "height": 800})
        page.goto(B + "/gui.html", wait_until="load", timeout=60000)
        page.wait_for_timeout(2000)
        page.locator("#pl-deepseek").click()
        time.sleep(3)

        results = {}
        # ── ① 逐个生成产物（严格串行，上一条落盘才发下一条，防队列堆积）──
        for tag, tool, fname, ext, content in PRODS:
            p = os.path.join(SESS, fname)
            print(f"\n══ 生成 {tag.upper()} ({tool} → {fname}) ══")
            page.locator("#input").fill(proto(tool, fname, content))
            page.locator("#sendBtn").click()
            print("  已发送，等待文件落盘 ...")
            landed = False
            for i in range(40):   # 最多 120s
                if os.path.exists(p):
                    data = open(p, "rb").read()
                    okfmt = valid(ext, data)
                    print(f"  [{i*3}s] 落盘 {len(data)} bytes, 格式{'正确' if okfmt else '错误!'}")
                    if okfmt:
                        landed = True
                        break
                time.sleep(3)
            results[tag] = {"file": p, "landed": landed, "ext": ext, "fname": fname}

        # ── ② 刷新侧边栏并逐个点击验收预览 ──
        print("\n══ 侧边栏预览验收 ══")
        page.evaluate("loadAttachments()")
        page.wait_for_timeout(3000)
        n_items = page.locator("#fileList .file-item").count()
        print(f"  侧边栏 #fileList 现有 {n_items} 个文件")

        for tag, tool, fname, ext, content in PRODS:
            if not results[tag]["landed"]:
                print(f"  [{tag}] 文件未落盘，跳过预览")
                continue
            clicked = False
            for it in page.locator("#fileList .file-item").all():
                if fname in (it.get_attribute("title") or it.inner_text() or ""):
                    it.click()
                    clicked = True
                    break
            page.wait_for_timeout(3000)
            pv = page.evaluate("(function(){var c=document.getElementById('filePreviewContainer');"
                               "var ta=document.getElementById('editTextarea');"
                               "return {txt:(c&&c.innerText||'').slice(0,160), ta: ta?ta.value.slice(0,120):null};"
                               "})()")
            body = (pv.get("txt") or "") + " " + (pv.get("ta") or "")
            expect = {"word": "Word产物验证成功", "ppt": "PPT", "pdf": "PDF", "code": "prod_code"}[tag]
            ok = clicked and (len(pv.get("txt") or "") > 10 or pv.get("ta") is not None) and expect.lower() in body.lower()
            print(f"  [{tag}] 点中={clicked}, 预览={'OK' if ok else '需人工看截图'}, 右栏={pv.get('txt','')[:90]!r} ta={(pv.get('ta') or '')[:60]!r}")
            results[tag]["preview"] = ok
            page.screenshot(path=os.path.join(APP, f"_prod_{tag}.png"))

        print("\n===== 汇总 =====")
        for tag in [x[0] for x in PRODS]:
            r = results[tag]
            print(f"  {tag:5s} 落盘={'Y' if r['landed'] else 'N'}  侧边栏点击预览={'Y' if r.get('preview') else '?'}  ({r['fname']})")
        browser.close()


if __name__ == "__main__":
    main()
