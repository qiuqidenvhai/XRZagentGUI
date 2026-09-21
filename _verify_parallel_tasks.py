# -*- coding: utf-8 -*-
"""_verify_parallel_tasks.py —— 验证「多任务真并行、不再排队」。

用户诉求（原话）：
  「子母代理不是可以在一个浏览器里开两个标签吗，那多任务当然可以呀，
    而且我还可以不同任务用不同的AI平台，
    怎么会出现上一个任务正在执行需要排队的离谱信息」

也就是说用户要看到：
  A) 同时发两条任务，两条【同时在跑】，而不是第二条等第一条跑完；
  B) 全程不出现「上一个任务正在执行 / 排队」这类提示；
  C) 两条任务各自拿到自己的回复，不串台。

本脚本用真机验证：连 SSE → 同一瞬间连发两条带唯一 token 的指令 →
观察两条流的 ai_final_reply 到达时间是否【重叠】、token 是否各归各位。

关键判据（T4）：两条任务的执行时间区间必须有交集。
  · 若被串行化（排队），区间必然首尾相接、几乎不重叠；
  · 真并行则区间显著重叠。

用法：python _verify_parallel_tasks.py
"""
import json
import re
import sys
import threading
import time
import urllib.request

API = "http://127.0.0.1:8888"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + str(detail)[:260] if detail else ""),
          flush=True)


def sse_collect(store, stop_evt):
    try:
        req = urllib.request.Request(API + "/events")
        with urllib.request.urlopen(req, timeout=900) as resp:
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
                    ev["_t"] = time.time()   # 本地到达时间，用于算区间重叠
                    store.append(ev)
                    continue
                buf += chunk
    except Exception as e:
        store.append({"_sse_error": repr(e), "_t": time.time()})


def post(path, obj, timeout=120):
    req = urllib.request.Request(
        API + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def send_async(cmd, platform, out, idx):
    """后台线程发指令；记录发出/返回时间。"""
    t0 = time.time()
    try:
        r = post("/command", {"command": cmd, "platform": platform}, timeout=600)
        out[idx] = {"ok": True, "resp": r, "t_send": t0, "t_ack": time.time()}
    except Exception as e:
        out[idx] = {"ok": False, "err": repr(e), "t_send": t0, "t_ack": time.time()}


def main():
    print("== 多任务并行验证（真机，禁用排队）==\n", flush=True)

    store = []
    stop = threading.Event()
    th = threading.Thread(target=sse_collect, args=(store, stop), daemon=True)
    th.start()
    time.sleep(2)

    ts = str(int(time.time()))[-5:]
    tokA = "PA" + ts
    tokB = "PB" + ts
    cmdA = f"请只回复这串字符，不要有任何其它文字：{tokA}"
    cmdB = f"请只回复这串字符，不要有任何其它文字：{tokB}"

    print(f"[1] 同一瞬间连发两条任务（token {tokA} / {tokB}）…", flush=True)
    out = {}
    tA = threading.Thread(target=send_async, args=(cmdA, "deepseek", out, "A"), daemon=True)
    tB = threading.Thread(target=send_async, args=(cmdB, "deepseek", out, "B"), daemon=True)
    tA.start()
    tB.start()
    tA.join(timeout=30)
    tB.join(timeout=30)
    print("    受理:", {k: (v.get("resp", {}).get("type") if v.get("ok") else v.get("err")) for k, v in out.items()},
          flush=True)
    for k in ("A", "B"):
        v = out.get(k) or {}
        check(f"任务{k} 被受理（accepted）",
              v.get("ok") and (v.get("resp") or {}).get("type") in ("accepted", "ok"),
              (v.get("resp") or {}).get("type") or v.get("err"))

    # 等两条各自的 final
    print("\n[2] 等两条任务各自的最终回复…", flush=True)
    t0 = time.time()
    while time.time() - t0 < 400:
        finals = [e for e in store if e.get("type") == "ai_final_reply"]
        if len(finals) >= 2:
            break
        time.sleep(2)
    time.sleep(3)
    stop.set()

    evs = [e for e in store if "_sse_error" not in e]
    types = {}
    for e in evs:
        types[e.get("type")] = types.get(e.get("type"), 0) + 1
    print("    事件统计:", types, flush=True)

    # ── 排队话术必须彻底消失 ──
    qwords = ("排队", "上一个任务", "上一条任务", "还在执行")
    hits = []
    for e in evs:
        blob = json.dumps(e, ensure_ascii=False)
        for w in qwords:
            if w in blob:
                hits.append((e.get("type"), w, blob[:160]))
    check("T1 全程无「排队/上一个任务还在执行」提示", len(hits) == 0,
          f"命中={len(hits)} {hits[:2]}")

    # 终端命令侧也不该出现排队（/command 返回值）
    ack_blob = json.dumps(out, ensure_ascii=False)
    bad_ack = [w for w in qwords if w in ack_blob]
    check("T2 /command 受理响应里无排队话术", len(bad_ack) == 0, bad_ack)

    # ── 两条任务都拿到了自己的回复 ──
    finals = [e for e in evs if e.get("type") == "ai_final_reply"]
    check("T3 收到 2 条 ai_final_reply", len(finals) >= 2, f"count={len(finals)}")

    # ── 核心：执行区间是否重叠 ──
    # 用「第一条 ai_thinking/thinking/tool_start 出现」到「该任务 final 出现」算一个区间。
    # 由于两者 token 不同，先按 token 归属 event。
    def belongs(ev, tok):
        return tok in json.dumps(ev, ensure_ascii=False)

    def window(tok):
        rel = [e for e in evs if belongs(e, tok)]
        if not rel:
            return None
        t_start = min(e["_t"] for e in rel)
        t_end = max(e["_t"] for e in rel)
        return (t_start, t_end)

    wA = window(tokA)
    wB = window(tokB)
    print(f"    区间A={wA}  区间B={wB}", flush=True)

    if wA and wB:
        ov = min(wA[1], wB[1]) - max(wA[0], wB[0])
        durA = wA[1] - wA[0]
        durB = wB[1] - wB[0]
        shortest = min(durA, durB) or 1e-6
        ratio = ov / shortest
        check("T4 两条任务执行区间【重叠】（真并行，非排队）",
              ov > 0 and ratio > 0.15,
              f"重叠={ov:.1f}s  A历时={durA:.1f}s B历时={durB:.1f}s 重叠比={ratio:.2f}")
    else:
        check("T4 两条任务执行区间【重叠】（真并行，非排队）", False,
              f"无法归因 token（A={wA} B={wB}）—— 可能两条都空回复")

    # ── token 各归各位（不串台）──
    a_tok_in_b = any(belongs(e, tokA) and belongs(e, tokB) for e in finals)
    check("T5 两条任务的 token 没有混在同一条回复里", not a_tok_in_b, "")

    if wA and wB:
        gap = max(wA[0], wB[0]) - min(wA[1], wB[1])
        print(f"    （参考）若为串行，间隔应 >0 且约等于另一条历时；实测首尾间隔={gap:.1f}s", flush=True)

    print("\n== 汇总 ==", flush=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for n, ok, _ in RESULTS:
        print(("PASS " if ok else "FAIL ") + n, flush=True)
    print(f"\n{passed}/{len(RESULTS)} 通过", flush=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
