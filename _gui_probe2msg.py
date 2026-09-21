#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""决定性实验：同会话连发 2 条【短】消息，逐条看豆包到底回不回。
目的：证明/证伪「静默失效是我把多轮搞坏了」。
  · 第 1 条就静默（0 气泡）→ 平台侧（6.8KB 重打包/风控/额度）
  · 第 1 条有回复、第 2 条才静默 → 我的多轮/rollover 代码把活会话弄死
每条等 done 或等 90s 超时（短任务不该 540s），抓 SSE 工具序列 + 母代理最终回复。
"""
import socket, json, time, threading, os, sys
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8888"
APP = os.path.dirname(os.path.abspath(__file__))
PLAT = sys.argv[1] if len(sys.argv) > 1 else "doubao"
MSG1 = "用一句话告诉我：多肉植物为什么耐旱？"
MSG2 = "上一条你答的对不对？再补 1 条多肉植物光照要点。"
TURN_WAIT = 90   # 短任务 90s 上限


class SSEClient:
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", 8888), timeout=3)
        self.s.sendall(b"GET /events HTTP/1.1\r\nHost:127.0.0.1:8888\r\nAccept:text/event-stream\r\n\r\n")
        self.buf = b""; self.items = []; self.lock = threading.Lock(); self._stop = False
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
                    self.items.append({"type": e.get("type"), "data": e.get("data")})

    def stop(self):
        self._stop = True
        try: self.s.close()
        except Exception: pass

    def snapshot(self):
        with self.lock:
            return list(self.items)


def mother_done(new_evs):
    """本轮母代理是否 done（排除子代理事件）"""
    for it in new_evs:
        d = it.get("data")
        if not isinstance(d, dict) or "subagent_task_id" in d:
            continue
        if it["type"] == "tool_end" and d.get("tool") == "done":
            return True
    return False


def last_final(new_evs):
    for it in reversed(new_evs):
        d = it.get("data")
        if it["type"] == "ai_final_reply" and isinstance(d, dict):
            return (d.get("text") or str(d.get("data") or ""))[:300]
    return ""


def wait_turn(sse, off, wait=TURN_WAIT):
    t0 = time.time()
    while time.time() - t0 < wait:
        snap = sse.snapshot()
        if mother_done(snap[off:]):
            return True, snap[off:]
        time.sleep(3)
    return False, sse.snapshot()[off:]


def wait_platform_ready(target, timeout=150):
    """切到目标平台后，轮询 /health 的 platform 字段真正 = target 才放行。
    修复致命时序坑：平台切换是异步的（豆包浏览器 launch+navigate 可达 20s+），
    切换未完成时就发指令会被路由到【旧平台】（默认 DeepSeek）→ 于是出现
    「MSG1 被 DeepSeek 答了、MSG2 才真正打到 豆包」的跨平台假象，
    让整个 豆包 多轮诊断被污染。必须等 /health platform=target 再发。"""
    import urllib.request
    t0 = time.time(); last = "?"
    while time.time() - t0 < timeout:
        try:
            r = urllib.request.urlopen(B + "/health", timeout=4)
            j = json.loads(r.read().decode("utf-8", "ignore"))
            last = str(j.get("platform"))
            if j.get("platform") == target:
                print("   /health platform=%s 就绪（切换耗时 %.0fs）" % (last, time.time()-t0), flush=True)
                return True
        except Exception as e:
            last = "ERR:%s" % e
        time.sleep(3)
    print("   !! 平台切换超时：/health 最后=%s（期望 %s）" % (last, target), flush=True)
    return False


def main():
    sse = SSEClient(); time.sleep(3); off = len(sse.snapshot())
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--disable-gpu","--no-sandbox","--use-gl=swiftshader"])
        pg = br.new_page(viewport={"width":1280,"height":820})
        pg.goto(B + "/gui.html", wait_until="load", timeout=60000)
        pg.wait_for_timeout(2500)
        # 读当前平台，若已是目标则跳过切换（保留登录态）；否则点击并【等真切完】
        import urllib.request as _ur
        try:
            cur = json.loads(_ur.urlopen(B + "/health", timeout=4).read().decode())["platform"]
        except Exception:
            cur = ""
        btn = pg.locator("#pl-" + PLAT)
        print("== 当前平台=%s 目标=%s 按钮存在=%s ==" % (cur, PLAT, btn.count() > 0), flush=True)
        if cur == PLAT:
            print("   后端已在 %s，跳过切换" % PLAT, flush=True)
        else:
            if btn.count() > 0:
                btn.click()
            wait_platform_ready(PLAT, timeout=150)
        results = []
        for tag, msg in (("MSG1", MSG1), ("MSG2", MSG2)):
            pg.locator("#input").fill(msg); pg.locator("#sendBtn").click()
            print("\n-- %s 已发: %s --" % (tag, msg), flush=True)
            t0 = time.time()
            ok, new_evs = wait_turn(sse, off)
            off = len(sse.snapshot())
            seq = [it["data"].get("tool") for it in new_evs
                   if it["type"] == "tool_start" and isinstance(it.get("data"), dict)
                   and "subagent_task_id" not in it["data"]]
            final = last_final(new_evs)
            results.append({"tag": tag, "done": ok, "sec": round(time.time()-t0,1), "seq": seq, "final": final})
            print("   %s done=%s 耗时=%.0fs 工具序列=%s" % (tag, ok, time.time()-t0, seq), flush=True)
            print("   %s 最终回复: %s" % (tag, final or "(无)"), flush=True)
            pg.wait_for_timeout(3000)
        pg.screenshot(path=os.path.join(APP, "_probe2msg_%s.png" % PLAT))
        dom = pg.evaluate("document.body.innerText")
        dom_info = {"dom_msgs": [MSG1 in dom, MSG2 in dom]}
        br.close()
    sse.stop()
    results.append(dom_info)
    json.dump(results, open(os.path.join(APP, "_probe2msg_%s.json" % PLAT), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n===== 判定 =====")
    for r in [x for x in results if isinstance(x, dict) and "tag" in x]:
        print("  %s: done=%s seq=%s final=%s" % (r["tag"], r["done"], r["seq"], r["final"][:80] or "(无)"))
    m1 = next((x for x in results if x.get("tag")=="MSG1"), {})
    m2 = next((x for x in results if x.get("tag")=="MSG2"), {})
    if m1.get("done"):
        print("结论: 第1条有回复 → 平台没死。第2条 done=%s → %s" %
              (m2.get("done"), "我多轮代码把活会话弄死了(真bug)" if not m2.get("done") else "两条都通,多轮正常"))
    else:
        print("结论: 第1条就静默(0工具事件) → 平台侧问题(6.8KB重打包/风控/额度),非多轮代码")
    print("完成。", flush=True)


if __name__ == "__main__":
    main()
