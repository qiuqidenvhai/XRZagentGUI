#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子代理端到端验证（v23，含 task 注入修复 + 子代理事件转发 + gui.html 子代理卡片）。
红线：走真实 GUI（Playwright 驱动 gui.html），同时影子 SSE 抓 /events 事件流。

验证目标（三件套）：
  ① 子代理真跑通：母代理 task 派子代理，子代理 result.json success=True
     （证明 spawn_child 注入 bug 修好，不再"母代理浏览器未就绪"）
  ② GUI 渲染链路：/events 里出现带 subagent_task_id 标记的事件（≥3 条）
     （证明子代理内部多轮工具调用现在能冒泡到 GUI，卡片能画）
  ③ 母代理产物：子代理发现被写成 docx 落盘 + 侧边栏可预览
  全程不得出现"母代理浏览器未就绪"错误。
"""
import socket, json, time, threading, os, glob
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8888"
APP = os.path.dirname(os.path.abspath(__file__))
SESS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session"
RPT = SESS + r"\subagent_e2e_report.docx"

INSTR = ("【必须使用子代理，不得用常识兜底】请严格按这个顺序、分多步执行：\n"
         "第1步：必须用 task 工具（type=research）委派一个 research 子代理，让它研究『仙人掌上 2 个有趣冷知识』并总结成 2 条。不要跳过这一步。\n"
         "第2步：用 wait_task 等子代理跑完，拿到它的发现。\n"
         "第3步：把子代理的发现用 docx_create 写成 Word 文档 " + RPT + "（标题『子代理冷知识报告』，正文含 2 条冷知识）。\n"
         "全部做完后调用 done。\n"
         "注意：即使子代理失败，也必须先真实调用 task + wait_task 拿到结果，再由你决定下一步；禁止一开始就跳过 task 用常识代替。")


class SSEClient:
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", 8888), timeout=3)
        self.s.sendall(b"GET /events HTTP/1.1\r\nHost:127.0.0.1:8888\r\nAccept:text/event-stream\r\n\r\n")
        self.buf = b""
        self.items = []
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
                                       "type": e.get("type"), "data": e.get("data")})

    def stop(self):
        self._stop = True
        try:
            self.s.close()
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return list(self.items)


def find_latest_subagent_result(since_ts=0.0):
    """找到 mtime>=since_ts 的最新 subagent_*/result.json（避免误捞历史旧结果）。"""
    pats = [
        SESS.replace("\\", "/") + "/subagent_*/result.json",
        r"D:/软件/XianRenZhangAgent/xrz_data/**/subagent_*/result.json",
        r"D:/软件/XianRenZhangAgent/xrz_data/*/subagent_*/result.json",
    ]
    cands = []
    for p in pats:
        cands += glob.glob(p, recursive=True)
    if since_ts:
        cands = [p for p in cands if os.path.getmtime(p) >= since_ts]
    if not cands:
        return None
    cands.sort(key=lambda x: os.path.getmtime(x))
    return cands[-1]


def main():
    run_start_ts = time.time() - 5  # 新鲜度锚点：只认本次运行后才创建的 result.json
    try:
        os.remove(RPT)
    except Exception:
        pass
    os.makedirs(SESS, exist_ok=True)

    sse = SSEClient()
    time.sleep(3)
    off = len(sse.snapshot())

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True,
                               args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        pg = br.new_page(viewport={"width": 1280, "height": 820})
        pg.goto(B + "/gui.html", wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        pg.locator("#pl-deepseek").click()
        time.sleep(4)

        print("══ 发子代理任务（task 派子代理 → wait_task → docx）══", flush=True)
        pg.locator("#input").fill(INSTR)
        pg.locator("#sendBtn").click()

        # 等母代理 docx 落盘（最多 6 分钟，子代理多轮较慢）
        land = False
        res_path = None
        for i in range(120):
            if os.path.exists(RPT):
                land = True
                break
            res_path = find_latest_subagent_result()
            time.sleep(3)
        time.sleep(10)  # 让 GUI 渲染 + SSE 收尾
        n_cards = pg.locator(".subagent-block").count()
        print("  子代理卡片数（DOM .subagent-block）=%d" % n_cards, flush=True)
        pg.screenshot(path=os.path.join(APP, "_subagent_e2e.png"))
        br.close()

    sse.stop()
    evs = sse.snapshot()[off:]
    sub_events = [it for it in evs
                  if isinstance(it["data"], dict) and it["data"].get("subagent_task_id")]
    types_sub = {}
    for it in sub_events:
        d = it["data"]
        k = d.get("subagent_type") or it["type"]
        types_sub[k] = types_sub.get(k, 0) + 1

    # 子代理 result.json
    res_path = find_latest_subagent_result()
    sub_success, sub_err = None, None
    if res_path:
        try:
            rd = json.load(open(res_path, encoding="utf-8"))
            sub_success = rd.get("success")
            sub_err = rd.get("error")
            print("  子代理 result.json: %s success=%s error=%s" %
                  (res_path, sub_success, (sub_err or "")[:120]), flush=True)
        except Exception as e:
            sub_err = "解析失败 %s" % e
    else:
        print("  未找到子代理 result.json（LLM 可能走了兜底常识路径）", flush=True)

    # 是否出现"母代理浏览器未就绪"
    joined = " ".join(json.dumps(it, ensure_ascii=False) for it in evs)
    no_ready_err = ("母代理浏览器未就绪" not in joined) and (sub_err or "").__class__ is str and "母代理浏览器未就绪" not in (sub_err or "")

    rpt_ok = os.path.exists(RPT) and open(RPT, "rb").read()[:2] == b"PK"

    print("\n===== 判定 =====")
    print("  ① 子代理真跑通(result.json success): %s" % (sub_success if sub_success is not None else "未触发/兜底"))
    print("  ② 母浏览器未就绪错误: %s" % ("无（注入修复生效）" if no_ready_err else "出现（仍有问题）"))
    print("  ③ GUI 子代理事件冒泡: %d 条 (types=%s)" % (len(sub_events), types_sub))
    print("  ④ 母代理 docx 落盘: %s" % ("Y" if rpt_ok else "N"))

    with open(os.path.join(APP, "_subagent_e2e_events.json"), "w", encoding="utf-8") as f:
        json.dump({"sub_events": sub_events, "all": evs, "sub_result": res_path,
                   "sub_success": sub_success, "sub_err": sub_err,
                   "no_ready_err": no_ready_err, "rpt_ok": rpt_ok},
                  f, ensure_ascii=False, indent=2)
    print("\n完成。", flush=True)


if __name__ == "__main__":
    main()
