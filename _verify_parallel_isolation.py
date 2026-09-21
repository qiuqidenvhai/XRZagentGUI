# -*- coding: utf-8 -*-
"""_verify_parallel_isolation.py —— 并发任务【不串台】的判定性验证。

════════════════════════════════════════════════════════════════════════
这一版要证的核心命题（比「有没有排队提示」更硬）
════════════════════════════════════════════════════════════════════════
上一版 _verify_parallel_tasks.py 只验证了「两条任务时间区间重叠」。
但真机翻车记录显示：区间重叠 ≠ 正确 —— 第一版补丁让两条任务并行跑起来了，
结果任务 A（token PA62373）拿到的回复却是 **PB62373**（任务 B 的答案），
两条消息还挤进了同一个会话文件。所以「并行」必须和「隔离」一起验。

本脚本的判定标准：
  T1 两条任务各自的 token 都出现在【自己的】回复里（A→PAxxx, B→PBxxx）
  T2 没有一条回复同时含两个 token（没有混在一起）
  T3 两条任务分别落在【两个不同的】会话文件里（不共用一个会话上下文）
  T4 每个会话文件里只出现自己的 token（A 的文件不含 PB，B 的文件不含 PA）
  T5 全程没有「排队 / 上一个任务还在执行」提示
  T6 两条任务都收到了最终回复

用法：python _verify_parallel_isolation.py
"""
import glob
import json
import os
import sys
import threading
import time
import urllib.request

API = "http://127.0.0.1:8888"
RESULTS = []
CONV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "xrz_data", "XianRenZhang_tasks", "conversations")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + str(detail)[:240] if detail else ""),
          flush=True)


def sse_collect(store, stop_evt):
    try:
        with urllib.request.urlopen(API + "/events", timeout=900) as resp:
            buf = b""
            while not stop_evt.is_set():
                ch = resp.read(1)
                if not ch:
                    break
                if ch in (b"\n", b"\r"):
                    if not buf:
                        continue
                    line = buf.decode("utf-8", "replace")
                    buf = b""
                    if not line.startswith("data:"):
                        continue
                    p = line[5:].strip()
                    if not p:
                        continue
                    try:
                        ev = json.loads(p)
                    except Exception:
                        continue
                    ev["_t"] = time.time()
                    store.append(ev)
                    continue
                buf += ch
    except Exception as e:
        store.append({"_sse_error": repr(e), "_t": time.time()})


def post(path, obj, timeout=120):
    req = urllib.request.Request(
        API + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def snapshot_conv_files():
    return set(glob.glob(os.path.join(CONV_DIR, "conv_*.json")))


def main():
    print("== 并发任务隔离验证（真机）==\n", flush=True)

    before = snapshot_conv_files()
    store = []
    stop = threading.Event()
    threading.Thread(target=sse_collect, args=(store, stop), daemon=True).start()
    time.sleep(2)

    ts = str(int(time.time()))[-5:]
    tokA, tokB = "IA" + ts, "IB" + ts
    cmdA = f"请只回复这串字符，不要有任何其它文字：{tokA}"
    cmdB = f"请只回复这串字符，不要有任何其它文字：{tokB}"

    print(f"[1] 同时发两条任务（{tokA} / {tokB}）…", flush=True)
    res = {}

    def fire(tag, cmd):
        try:
            res[tag] = post("/command", {"command": cmd, "platform": "deepseek"}, timeout=600)
        except Exception as e:
            res[tag] = {"type": "ERR", "err": repr(e)}

    thA = threading.Thread(target=fire, args=("A", cmdA), daemon=True)
    thB = threading.Thread(target=fire, args=("B", cmdB), daemon=True)
    thA.start(); thB.start()
    thA.join(30); thB.join(30)
    print("    受理:", {k: v.get("type") for k, v in res.items()}, flush=True)

    print("\n[2] 等两条 final…", flush=True)
    t0 = time.time()
    while time.time() - t0 < 400:
        if len([e for e in store if e.get("type") == "ai_final_reply"]) >= 2:
            break
        time.sleep(2)
    time.sleep(5)   # 留时间给落盘
    stop.set()

    evs = [e for e in store if "_sse_error" not in e]
    finals = [e for e in evs if e.get("type") == "ai_final_reply"]
    final_texts = [((e.get("data") or {}).get("text") or "") for e in finals]

    # T1 各拿到自己的
    a_own = any(tokA in t for t in final_texts)
    b_own = any(tokB in t for t in final_texts)
    check("T1 任务A/B 各自拿到自己的 token 回复", a_own and b_own,
          f"A得={a_own} B得={b_own} | " + " || ".join(t[:60].replace("\n", " ") for t in final_texts))

    # T2 没有混在一起
    mixed = [t for t in final_texts if tokA in t and tokB in t]
    check("T2 没有一条回复同时含两个 token", len(mixed) == 0, f"混合条数={len(mixed)}")

    # T3/T4 落盘隔离
    after = snapshot_conv_files()
    new_files = sorted(after - before)
    print(f"    新增会话文件 {len(new_files)} 个:", [os.path.basename(f) for f in new_files], flush=True)

    a_files, b_files, both_files = [], [], []
    for f in new_files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        blob = json.dumps(d, ensure_ascii=False)
        has_a, has_b = tokA in blob, tokB in blob
        if has_a and has_b:
            both_files.append(f)
        elif has_a:
            a_files.append(f)
        elif has_b:
            b_files.append(f)

    check("T3 两条任务落在不同的会话文件里（未共用一个上下文）",
          len(a_files) >= 1 and len(b_files) >= 1,
          f"A文件={len(a_files)} B文件={len(b_files)} 共有={len(both_files)}")

    check("T4 每个会话文件只含自己的 token（无交叉污染）",
          len(both_files) == 0,
          f"交叉文件={[os.path.basename(x) for x in both_files]}")

    # T5 无排队话术
    q = ("排队", "上一个任务", "上一条任务", "还在执行")
    hits = []
    for e in evs:
        blob = json.dumps(e, ensure_ascii=False)
        for w in q:
            if w in blob:
                hits.append(w)
    check("T5 全程无「排队/上一个任务还在执行」提示", len(hits) == 0, hits[:3])

    # T6 两条 final
    check("T6 收到 2 条 ai_final_reply", len(finals) >= 2, f"count={len(finals)}")

    print("\n== 汇总 ==", flush=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for n, ok, _ in RESULTS:
        print(("PASS " if ok else "FAIL ") + n, flush=True)
    print(f"\n{passed}/{len(RESULTS)} 通过", flush=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
