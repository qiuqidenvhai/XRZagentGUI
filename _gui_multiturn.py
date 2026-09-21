#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真·多轮 GUI 测试：同一个 GUI 会话连发 3 条用户消息，后一轮依赖前一轮的产出。

红线：走真实 GUI（Playwright 驱动 gui.html，#input 填 + #sendBtn 点），
影子 SSE 客户端监听 /events，等母代理本轮 done（非子代理事件）后才发下一条。

轮次设计（跨轮依赖链，不是 3 个独立任务）：
  T1 生成 Word 多肉养护.docx（控水/光照 2 要点）
  T2 在 T1 的同一个 docx 上追加 2 要点（通风/土壤）→ 必须保留原 2 条（跨轮上下文+文件延续）
  T3 基于 T2 的最新 docx 生成 PPT 多肉养护.pptx（每要点一页，跨轮转换）

判定：
  ① 每轮母代理 done 完成（tool_end done 且无 subagent_task_id 标记）
  ② docx 先含 2 要点，追加后含 通风+土壤 且原要点还在
  ③ pptx 生成、页数 ≥ 4
  ④ 全程同一个 GUI 会话（同一次 goto，连发 3 条，DOM 截图存证）

用法：python _gui_multiturn.py [deepseek|tongyi|doubao|yuanbao]
"""
import socket, json, time, threading, os, glob, sys
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8888"
APP = os.path.dirname(os.path.abspath(__file__))
PLAT = sys.argv[1] if len(sys.argv) > 1 else "deepseek"
SESS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session"
TASKS = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks"
TURN_TIMEOUT = int(os.environ.get("TURN_TIMEOUT", "540"))

T1 = ("把多肉植物养护的 2 个要点写进 Word 文档「多肉养护.docx」（标题 + 正文 2 条：控水、光照）。"
      "不需要上网搜索，直接凭知识写 2 条要点即可。完成后调用 done。")
T2 = ("在上一轮生成的 Word 文档「多肉养护.docx」里再追加 2 个要点：③通风 ④土壤/配土。"
      "先 file_read 读该文件现有内容，再把完整 4 条要点合并后覆盖保存到同一个文件，"
      "原有 2 条（控水、光照）不能丢。完成后调用 done。")
T3 = ("基于「多肉养护.docx」当前的内容生成 PPT「多肉养护.pptx」（先 file_read 读文件），"
      "封面 1 页 + 每个要点 1 页，共 5 页。完成后调用 done。")

TURNS = [
    ("T1", T1, {}),
    ("T2", T2, {"docx_must": ["通风", "土壤"]}),
    ("T3", T3, {"pptx_min_slides": 5}),
]


class SSEClient:
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", 8888), timeout=3)
        self.s.sendall(b"GET /events HTTP/1.1\r\nHost:127.0.0.1:8888\r\nAccept:text/event-stream\r\n\r\n")
        self.buf = b""
        self.items = []
        self.lock = threading.Lock()
        self._stop = False
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
                    self.items.append({"type": e.get("type"), "data": e.get("data"),
                                       "ts": time.time()})

    def stop(self):
        self._stop = True
        try:
            self.s.close()
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return list(self.items)


def fresh_docs(pattern, since_ts, dirs=(SESS, TASKS)):
    out = []
    for d in dirs:
        for f in glob.glob(os.path.join(d, pattern)):
            if os.path.getmtime(f) >= since_ts:
                out.append(f)
    return out


def docx_text(path):
    try:
        import docx as _d
        return "\n".join(p.text for p in _d.Document(path).paragraphs)
    except Exception:
        return ""


def pptx_slides(path):
    try:
        from pptx import Presentation
        return len(Presentation(path).slides)
    except Exception:
        return -1


def wait_mother_done(sse, off, label):
    """等母代理本轮 done（排除子代理事件）。
    若收到 ai_final_reply(type=error, 含"调用失败") → 立即判失败退出（不盲等超时）。
    返回 (ok, events_since, err_hint)"""
    t0 = time.time()
    last_err = ""
    fatal = ""
    while time.time() - t0 < TURN_TIMEOUT:
        snap = sse.snapshot()
        new = snap[off:]
        for it in new:
            d = it.get("data")
            if not isinstance(d, dict):
                continue
            if "subagent_task_id" in d:
                continue
            if it["type"] in ("tool_error", "command_error"):
                last_err = str(d)[:200]
            if it["type"] == "ai_final_reply" and d.get("type") == "error":
                txt = str(d.get("text") or d.get("data") or "")
                if "调用失败" in txt or "未登录" in txt or "失败" in txt:
                    fatal = "AI立即失败: " + txt[:160]
        if fatal:
            print("   !! %s %s" % (label, fatal), flush=True)
            return False, sse.snapshot()[off:], fatal
        for it in reversed(new):
            d = it.get("data")
            if (it["type"] == "tool_end" and isinstance(d, dict)
                    and d.get("tool") == "done" and "subagent_task_id" not in d):
                return True, new, last_err
        time.sleep(3)
    return False, sse.snapshot()[off:], last_err


def wait_platform_ready(pg, target, timeout=150):
    """点平台按钮后等 /health 的 platform 字段 = target（后端真正切完才放行）。
    修复时序坑：切换是异步的（浏览器 launch+navigate 可达数十秒），
    切换未完成时发出的指令会被路由到【旧平台】session（实测打到未登录的元宝 →
    AI 调用立即失败，全程零工具事件，脚本盲等 540s 白等）。"""
    t0 = time.time()
    last = "?"
    while time.time() - t0 < timeout:
        try:
            import urllib.request
            r = urllib.request.urlopen("http://127.0.0.1:8888/health", timeout=4)
            j = json.loads(r.read().decode("utf-8", "ignore"))
            last = str(j.get("platform"))
            if j.get("platform") == target:
                print("   /health platform=%s 就绪（切换耗时 %.0fs）" % (last, time.time() - t0), flush=True)
                return True
        except Exception as e:
            last = "ERR:%s" % e
        time.sleep(3)
    print("   !! 平台切换超时：/health 最后=%s（期望 %s）" % (last, target), flush=True)
    return False


def main():
    run_start_ts = time.time()
    sse = SSEClient()
    time.sleep(3)
    off = len(sse.snapshot())
    off_ts = sse.snapshot()[-1]["ts"] if sse.snapshot() else run_start_ts

    report = {"platform": PLAT, "turns": [], "artifacts": {}, "verdicts": []}
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True,
                               args=["--disable-gpu", "--no-sandbox", "--use-gl=swiftshader"])
        pg = br.new_page(viewport={"width": 1280, "height": 820})
        pg.goto(B + "/gui.html", wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        # 切到目标平台（同一会话，后续轮次不再切）
        btn = pg.locator("#pl-" + PLAT)
        print("══ 切换到平台 %s（按钮存在=%s），同一 GUI 会话连发 3 轮 ══" % (PLAT, btn.count() > 0), flush=True)
        switch_ok = True
        cur_plat = ""
        try:
            import urllib.request as _ur
            cur_plat = json.loads(_ur.urlopen("http://127.0.0.1:8888/health", timeout=4).read().decode())["platform"]
        except Exception:
            cur_plat = ""
        if cur_plat == PLAT:
            print("   后端已在 %s（登录态保留），跳过切换点击，直接开测" % PLAT, flush=True)
        else:
            print("   后端当前=%s，点击 #pl-%s 切换" % (cur_plat or "?", PLAT), flush=True)
            if btn.count() > 0:
                btn.click()
            switch_ok = wait_platform_ready(pg, PLAT, timeout=150)   # 等 /health 真切完才放行
            if not switch_ok:
                report["fatal"] = "平台切换未完成即发送"

        all_ok = True
        for label, text, checks in TURNS:
            if not switch_ok:
                print("!! 平台未切换完成，跳过全部轮次", flush=True)
                break
        for label, text, checks in TURNS:
            pg.locator("#input").fill(text)
            pg.locator("#sendBtn").click()
            print("── %s 已发送（%d 字），等母代理 done（上限 %ds）──" % (label, len(text), TURN_TIMEOUT), flush=True)
            t0 = time.time()
            ok, new_evs, err_hint = wait_mother_done(sse, off, label)
            off = len(sse.snapshot())
            seq = [it["data"].get("tool") for it in new_evs
                   if it["type"] == "tool_start" and isinstance(it["data"], dict)
                   and "subagent_task_id" not in it["data"]]
            print("   %s 完成=%s 耗时=%.0fs 母代理工具序列=%s" % (label, ok, time.time() - t0, seq), flush=True)
            if err_hint:
                print("   %s 轮内错误: %s" % (label, err_hint), flush=True)

            pg.wait_for_timeout(8000)   # 稳定后截图存证
            shot = os.path.join(APP, "_multiturn_%s_%s.png" % (PLAT, label))
            pg.screenshot(path=shot)

            # 轮后产物校验
            turn_rec = {"label": label, "done": ok, "seq": seq, "screenshot": shot,
                         "checks": {}}
            for f in fresh_docs("多肉养护.docx", off_ts):
                txt = docx_text(f)
                keys = []
                for kw in ["控水", "浇水", "光照", "通风", "土壤", "配土"]:
                    if kw in txt:
                        keys.append(kw)
                if label == "T1":
                    okc = len(keys) >= 2
                    turn_rec["checks"]["docx"] = {"file": f, "hit": keys, "pass": okc,
                                                   "text_head": txt[:120]}
                elif label == "T2":
                    need = checks.get("docx_must", [])
                    keep = ["控水", "浇水", "光照"]
                    okc = all(k in txt for k in need) and any(k in txt for k in keep)
                    turn_rec["checks"]["docx"] = {"file": f, "text": txt, "pass": okc}
                elif label == "T3":
                    turn_rec["checks"]["docx"] = {"file": f, "text": txt,
                                                   "hit": keys, "pass": True}
            if "pptx_min_slides" in checks:
                pptxs = fresh_docs("多肉养护.pptx", off_ts)
                if pptxs:
                    p = pptxs[0]
                    n = pptx_slides(p)
                    turn_rec["checks"]["pptx"] = {"file": p, "slides": n,
                                                  "pass": n >= checks["pptx_min_slides"]}
                else:
                    turn_rec["checks"]["pptx"] = {"file": None, "slides": None, "pass": False}
            report["turns"].append(turn_rec)
            all_ok = all_ok and ok and all(c["pass"] for c in turn_rec["checks"].values())
            if not ok:
                print("!! %s 未完成，后续轮次失去依赖前提，提前停止" % label, flush=True)
                break

        # DOM 存证：同一会话里 3 条用户消息都在
        dom_text = pg.evaluate("document.body.innerText")
        n_user_msgs = sum(1 for kw in ["多肉植物养护的 2 个要点", "再追加 2 个要点", "生成 PPT"] if kw in dom_text)
        report["dom_user_messages_seen"] = n_user_msgs
        br.close()

    sse.stop()
    report["all_ok"] = all_ok
    with open(os.path.join(APP, "_multiturn_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n===== 判定（平台=%s）=====" % PLAT, flush=True)
    for t in report["turns"]:
        print("  %s done=%s seq=%s checks=%s" % (
            t["label"], t["done"], t["seq"],
            {k: (v.get("pass") if isinstance(v, dict) else v) for k, v in t["checks"].items()}))
    print("  ① 三轮全部母代理 done: %s" % all(t["done"] for t in report["turns"]))
    print("  ② T2 跨轮追加(通风+土壤且原要点保留): %s" %
          (next((t["checks"]["docx"].get("pass") for t in report["turns"] if t["label"] == "T2"), None)))
    print("  ③ T3 PPT 页数: %s" %
          (next((t["checks"]["pptx"].get("slides") for t in report["turns"] if t["label"] == "T3" and "pptx" in t["checks"]), None)))
    print("  ④ 同一 GUI 会话 DOM 用户消息数: %s" % n_user_msgs)
    print("完成。报告: _multiturn_report.json", flush=True)


if __name__ == "__main__":
    main()
