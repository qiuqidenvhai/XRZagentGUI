#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实多轮 + 子代理 验收。
红线：走真实 GUI（Playwright 驱动 gui.html，点侧边栏/输入框/发送按钮），
     同时开一个「影子 SSE 客户端」抓 /events 事件流，用来铁证多轮/子代理真实发生。
不再塞 @@@@ 强制协议——让 LLM 自己决定调哪些工具、走多少轮。

判定维度：
  A) 真实多轮：任务 A 让 LLM 自然走 file_write→docx_create→file_read→docx_create，
     事件流里 tool_start≥3、不同工具≥2 → 真多轮。
  B) 子代理：任务 B 让母代理用 task 派研究子代理，母代理侧冒泡 task/check_task/wait_task；
     子代理内部工具（browser_search 等）按架构【不会】冒泡到 /events（子 commander 没接 GUI log）
     → 这是「子代理在 GUI 无专门渲染」的缺口铁证，脚本会把它标出来。
  C) 产物验收：notepad_test.txt / 报告.docx / 子代理报告.docx 落盘 + GUI 侧边栏 #fileList + 点击预览。
"""
import socket, json, time, threading, os
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8888"
APP = os.path.dirname(os.path.abspath(__file__))
SESS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session"
NB = SESS + r"\notepad_test.txt"
RPT = SESS + r"\报告.docx"
SBRPT = SESS + r"\子代理报告.docx"

A_TXT = ("请严格按顺序、分多步执行，不要一次性口述：\n"
         "第1步：用 file_write 创建文件 " + NB + " ，内容只写一行：hello xrz multi turn ok\n"
         "第2步：用 docx_create 生成 Word 文档 " + RPT + " ，标题「多轮验证」，正文「多轮任务验证成功」。\n"
         "第3步：用 file_read 读回 notepad_test.txt，把读到的内容追加进 报告.docx（再用 docx_create 重新生成一版含该内容）。\n"
         "全部做完后调用 done 收尾。")

B_TXT = ("请委派一个研究子代理（用 task 工具，type=research）调研「多肉植物/仙人掌多久浇一次水」，"
         "让它总结 3 条浇水要点。等子代理跑完后，用 check_task / wait_task 拿到结果，"
         "然后把这 3 条要点用 docx_create 写成 Word 文档 " + SBRPT + " ，并在最终回复里逐条列出这 3 条要点。"
         "全部做完后调用 done。")


class SSEClient:
    """影子 SSE 客户端：后台收 /events，逐条记录 (时刻, type, data)。"""
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", 8888), timeout=3)
        self.s.sendall(b"GET /events HTTP/1.1\r\nHost:127.0.0.1:8888\r\nAccept:text/event-stream\r\n\r\n")
        self.buf = b""
        self.items = []          # {"t":float,"type":str,"data":obj}
        self.lock = threading.Lock()
        self._stop = False
        self.t0 = time.time()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        self.s.settimeout(1.0)
        while not self._stop:
            try:
                c = self.s.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if not c:
                break
            self.buf += c
            while b"\n" in self.buf:
                line, _, self.buf = self.buf.partition(b"\n")
                line = line.strip()
                if not line.startswith(b"data: "):
                    continue
                try:
                    e = json.loads(line[6:])
                except Exception:
                    continue
                with self.lock:
                    self.items.append({"t": round(time.time() - self.t0, 1),
                                       "type": e.get("type"),
                                       "data": e.get("data")})

    def stop(self):
        self._stop = True
        try:
            self.s.close()
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return list(self.items)


def seg_stat(seg):
    tools = [it for it in seg if it["type"] == "tool_start"]
    ends = [it for it in seg if it["type"] == "tool_end"]
    think = sum(1 for it in seg if it["type"] in ("ai_thinking", "thinking"))
    finals = [it for it in seg if it["type"] == "ai_final_reply"]
    names = [(it.get("data") or {}).get("tool") for it in tools]
    return {
        "tool_start": len(tools), "tool_end": len(ends),
        "tool_names": names, "distinct_tools": sorted(set(n for n in names if n)),
        "thinking": think, "ai_final_reply": len(finals),
        "total_events": len(seg),
    }


def wait_files(files, timeout_s):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if all(os.path.exists(f) for f in files):
            return True
        time.sleep(3)
    return all(os.path.exists(f) for f in files)


def valid(ext, data):
    if ext in ("docx", "pptx", "xlsx"):
        return data[:2] == b"PK"
    if ext == "pdf":
        return data[:4] == b"%PDF"
    return len(data) > 0


def main():
    for f in (NB, RPT, SBRPT):
        try:
            os.remove(f)
        except Exception:
            pass
    os.makedirs(SESS, exist_ok=True)

    sse = SSEClient()
    time.sleep(3)  # drain 历史回放

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True,
                                args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        pg = br.new_page(viewport={"width": 1280, "height": 820})
        pg.goto(B + "/gui.html", wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        pg.locator("#pl-deepseek").click()
        time.sleep(4)

        offA = len(sse.snapshot())
        print("══ 任务 A：真实多轮（file_write→docx_create→file_read→docx_create）══", flush=True)
        pg.locator("#input").fill(A_TXT)
        pg.locator("#sendBtn").click()
        landA = wait_files([NB, RPT], 240)
        time.sleep(12)  # 让 LLM 收尾、面板渲染
        n_msg = pg.locator("#messages .msg").count()
        pg.screenshot(path=os.path.join(APP, "_multi_A.png"))
        offB = len(sse.snapshot())

        print("══ 任务 B：子代理（task 派研究子代理 + 产物 docx）══", flush=True)
        pg.locator("#input").fill(B_TXT)
        pg.locator("#sendBtn").click()
        landB = wait_files([SBRPT], 300)
        time.sleep(12)
        n_msg2 = pg.locator("#messages .msg").count()
        pg.screenshot(path=os.path.join(APP, "_multi_B.png"))
        br.close()

    sse.stop()
    evs = sse.snapshot()
    segA, segB = evs[offA:offB], evs[offB:]
    stA, stB = seg_stat(segA), seg_stat(segB)

    # 多轮判定（真实）
    multi_ok = stA["tool_start"] >= 3 and len(stA["distinct_tools"]) >= 2
    # 子代理判定
    sub_disp = ("task" in stB["tool_names"]) or ("browser_research" in stB["tool_names"])
    # 子代理内部工具是否冒泡到 /events（架构预期=不冒泡 → GUI 缺口）
    # 母代理侧子代理工具集 = {task, check_task, wait_task, get_subagent_result, browser_research, browser_visit}
    PARENT_SUB_TOOLS = {"task", "check_task", "wait_task", "get_subagent_result"}
    sub_internal_bubbles = [n for n in stB["distinct_tools"]
                            if n in PARENT_SUB_TOOLS]
    print("\n===== 任务 A 事件统计 =====", flush=True)
    print(json.dumps(stA, ensure_ascii=False, indent=2), flush=True)
    print("\n===== 任务 B 事件统计 =====", flush=True)
    print(json.dumps(stB, ensure_ascii=False, indent=2), flush=True)

    print("\n===== 判定 =====")
    print("  真实多轮 A：tool_start=%d 不同工具=%s → %s" %
          (stA["tool_start"], stA["distinct_tools"], "PASS" if multi_ok else "FAIL"))
    print("  子代理派发 B：母代理侧出现 task/browser_research=%s → %s" %
          (sub_disp, "PASS" if sub_disp else "未触发"))
    print("  子代理内部工具冒泡到 /events：%s（架构预期为空=缺口：子代理事件流未接 GUI）" %
          (sub_internal_bubbles if sub_internal_bubbles else "无（子代理内部多轮对 GUI 不可见）"))
    print("  产物落盘：notepad=%s 报告.docx=%s 子代理报告.docx=%s" %
          (landA and os.path.exists(NB), landA, landB))

    # dump 完整事件时间线
    with open(os.path.join(APP, "_multi_events.json"), "w", encoding="utf-8") as f:
        json.dump({"A": segA, "B": segB, "statA": stA, "statB": stB}, f, ensure_ascii=False, indent=2)

    # 侧边栏 + 预览验收
    print("\n===== 侧边栏预览验收 =====")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True,
                                args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        pg = br.new_page(viewport={"width": 1280, "height": 820})
        pg.goto(B + "/gui.html", wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        pg.locator("#pl-deepseek").click()
        time.sleep(2)
        pg.evaluate("loadAttachments()")
        pg.wait_for_timeout(2500)
        n_items = pg.locator("#fileList .file-item").count()
        print("  #fileList 现有 %d 个文件" % n_items)
        for name, expect in [("notepad_test.txt", "hello xrz"),
                             ("报告.docx", "多轮"),
                             ("子代理报告.docx", "水")]:
            p = os.path.join(SESS, name)
            if not os.path.exists(p):
                print("  [%s] 未落盘，跳过" % name)
                continue
            clicked = False
            for it in pg.locator("#fileList .file-item").all():
                if name in (it.get_attribute("title") or it.inner_text() or ""):
                    it.click(); clicked = True; break
            pg.wait_for_timeout(2800)
            pv = pg.evaluate("(function(){var c=document.getElementById('filePreviewContainer');"
                             "var ta=document.getElementById('editTextarea');"
                             "return {txt:(c&&c.innerText||'').slice(0,200), ta:ta?ta.value.slice(0,160):null};})()")
            body = (pv.get("txt") or "") + " " + (pv.get("ta") or "")
            ok = clicked and (len(pv.get("txt") or "") > 8 or pv.get("ta")) and expect in body
            print("  [%s] 点中=%s 预览=%s 右栏=%r" % (name, clicked, "OK" if ok else "需看截图", body[:110]))
            pg.screenshot(path=os.path.join(APP, "_multi_prev_%s.png" % ("txt" if name.endswith("txt") else ("rpt" if "报告" in name and "子" not in name else "sub"))))
        br.close()

    print("\n完成。", flush=True)


if __name__ == "__main__":
    main()
