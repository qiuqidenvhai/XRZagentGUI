# -*- coding: utf-8 -*-
"""_verify_thinking_stream.py —— 验证「思考过程」真的从网页流到 GUI。

背景（用户投诉）：「思考过程显示有大问题」。
已查明的根因：platforms.json 里 deepseek 的 thinking_selector 是空字符串，
覆盖了代码内置默认值 → platform_browser.wait_response 里的
`if thinking_sel and on_thinking:` 恒为假 → 整个任务过程中一次
ai_thinking 事件都不会发出 → GUI 的「🧠 模型推理（深度思考）」块永远空白
（只在收尾时补发一次空值，等于没有）。

本脚本做的事（全部真机、不 mock）：
  1) 连上后端 /events SSE；
  2) 通过 /command 发一条真指令（DeepSeek，深度思考已开）；
  3) 全程收集 SSE 事件，断言：
     T1 收到过 ai_thinking 事件（不是 0 次）；
     T2 至少一条 ai_thinking 的 text 非空且长度 >= 20（真推理正文）；
     T3 该 text 不含「已思考（用时」这类折叠条标题（不能被标题污染）；
     T4 收到了 ai_final_reply；
     T5 final reply 正文里不含「已思考（用时」；
     T6 真机 DOM 里 .ds-think-content .ds-markdown 有内容（与 T2 交叉印证）；
     T7 落盘的会话 json 里 assistant 内容不含思考标题。

用法：python _verify_thinking_stream.py
"""
import json
import sys
import threading
import time
import urllib.request

API = "http://127.0.0.1:8888"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + str(detail)[:220] if detail else ""),
          flush=True)


def sse_collect(store, stop_evt):
    """后台线程收 SSE 事件，按 type 归类存进 store。"""
    try:
        req = urllib.request.Request(API + "/events")
        with urllib.request.urlopen(req, timeout=600) as resp:
            buf = b""
            while not stop_evt.is_set():
                chunk = resp.read(1)
                if not chunk:
                    break
                if chunk in (b"\n", b"\r"):
                    if not buf:
                        continue
                    line = buf.decode("utf-8", "replace")
                    buf = b""
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except Exception:
                        continue
                    store.append(ev)
                    continue
                buf += chunk
    except Exception as e:
        store.append({"_sse_error": repr(e)})


def probe(js, timeout=40):
    """在【真实 agent 页面】上跑 JS（后端 /probe 端点，走它自己的事件循环）。"""
    try:
        req = urllib.request.Request(
            API + "/probe",
            data=json.dumps({"js": js}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
        return d
    except Exception as e:
        return {"type": "error", "note": repr(e)}


def wait_idle(max_s=180):
    """等平台上没有生成中信号（避免读到上一轮的残留）。"""
    js = ("(() => { return JSON.stringify({"
          " gen: document.querySelectorAll(\".loading, [class*='generating']\").length"
          "}); })()")
    stable = 0
    t0 = time.time()
    while time.time() - t0 < max_s:
        r = probe(js)
        if r.get("type") == "ok":
            try:
                v = json.loads(r["value"])
                if v.get("gen", 0) == 0:
                    stable += 1
                    if stable >= 3:
                        return True
                else:
                    stable = 0
            except Exception:
                pass
        time.sleep(2)
    return False


def find_latest_conv():
    from pathlib import Path
    import glob
    root = Path(__file__).resolve().parent
    cands = []
    for pat in ("xrz_data/**/conv_deepseek_*.json", "**/conv_deepseek_*.json"):
        cands += glob.glob(str(root / pat), recursive=True)
    cands = sorted(set(cands), key=lambda p: __import__("os").path.getmtime(p), reverse=True)
    return cands[0] if cands else None


def main():
    print("== 思考过程流式显示验证（真机）==", flush=True)

    print("\n[0] 等平台空闲…", flush=True)
    idle = wait_idle()
    print("    idle =", idle, flush=True)

    store = []
    stop = threading.Event()
    th = threading.Thread(target=sse_collect, args=(store, stop), daemon=True)
    th.start()
    time.sleep(2)

    token = "SZQA" + str(int(time.time()))[-4:]
    instr = ("请先在心里想清楚再回答。" + token)
    print("\n[1] 发送真指令:", instr, flush=True)
    try:
        req = urllib.request.Request(
            API + "/command",
            data=json.dumps({"command": instr, "platform": "deepseek"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        r = json.loads(urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "replace"))
        print("    accepted =", r.get("type"), flush=True)
        check("命令被受理（accepted）", r.get("type") in ("accepted", "ok"), r.get("type"))
    except Exception as e:
        check("命令被受理（accepted）", False, repr(e))

    # 等最终回复（最多 260s）
    print("\n[2] 等最终回复…", flush=True)
    t0 = time.time()
    while time.time() - t0 < 260:
        if any(e.get("type") == "ai_final_reply" for e in store):
            break
        time.sleep(2)
    time.sleep(3)
    stop.set()

    evs = [e for e in store if "_sse_error" not in e]
    types = {}
    for e in evs:
        types[e.get("type")] = types.get(e.get("type"), 0) + 1
    print("    事件统计:", types, flush=True)

    think_evts = [e for e in evs if e.get("type") == "ai_thinking"]
    finals = [e for e in evs if e.get("type") == "ai_final_reply"]

    # T1
    check("T1 收到过 ai_thinking 事件", len(think_evts) > 0, f"count={len(think_evts)}")

    # T2 非空推理正文
    texts = []
    for e in think_evts:
        d = e.get("data") or {}
        t = (d.get("text") or "").strip()
        if t:
            texts.append(t)
    substantive = [t for t in texts if len(t) >= 20]
    check("T2 至少一条 ai_thinking 有 >=20 字推理正文",
          len(substantive) > 0,
          f"max_len={max([len(t) for t in texts], default=0)} 条数={len(substantive)}")

    # T3 不含折叠条标题
    bad = [t for t in texts if "已思考（用时" in t or "已思考(用时" in t]
    check("T3 ai_thinking 文本不含「已思考（用时…）」标题", len(bad) == 0,
          f"污染条数={len(bad)}")

    # T4
    check("T4 收到 ai_final_reply", len(finals) > 0, f"count={len(finals)}")

    # T5 最终回复不含思考标题
    final_text = ""
    if finals:
        final_text = ((finals[-1].get("data") or {}).get("text") or "")
    check("T5 最终回复不含「已思考（用时…）」",
          "已思考（用时" not in final_text and "已思考(用时" not in final_text,
          final_text[:100])

    # T6 真机 DOM 交叉印证
    js = ("(() => {"
          " const m=document.querySelector('.ds-think-content .ds-markdown');"
          " return JSON.stringify({len: m ? (m.innerText||'').trim().length : 0,"
          "  head: m ? (m.innerText||'').trim().slice(0,60) : null}); })()")
    r = probe(js)
    dom_len = 0
    dom_head = ""
    if r.get("type") == "ok":
        try:
            v = json.loads(r["value"])
            dom_len = v.get("len", 0)
            dom_head = v.get("head") or ""
        except Exception:
            pass
    check("T6 真机 DOM .ds-think-content .ds-markdown 有正文", dom_len >= 20,
          f"len={dom_len} head={dom_head[:50]}")

    # T7 落盘会话记录干净
    cf = find_latest_conv()
    asst_bad = False
    asst_txt = ""
    if cf:
        try:
            data = json.loads(open(cf, encoding="utf-8").read())
            msgs = data.get("messages") or []
            asst = [m for m in msgs if (m.get("role") or "") == "assistant"]
            if asst:
                asst_txt = str(asst[-1].get("content") or "")
                asst_bad = ("已思考（用时" in asst_txt) or ("已思考(用时" in asst_txt)
        except Exception as e:
            asst_txt = "读取失败 " + repr(e)[:80]
    check("T7 落盘会话 assistant 内容不含思考标题",
          (not asst_bad) if cf else True,
          f"file={cf} content={asst_txt[:80]}")

    print("\n== 汇总 ==", flush=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for n, ok, d in RESULTS:
        print(("PASS " if ok else "FAIL ") + n, flush=True)
    print(f"\n{passed}/{total} 通过", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
