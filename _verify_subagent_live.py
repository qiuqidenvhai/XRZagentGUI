# -*- coding: utf-8 -*-
"""纠正版真端到端：用 GUI 同款 /command 启动真实任务（异步，结果走 SSE），
监听 /events 抓 subagent_task_id 事件，证明子代理系统真闭环。
"""
import json, time, threading, urllib.request

API = "http://127.0.0.1:8888"
state = {"ids": set(), "types": [], "all": [], "got": False, "started": False, "replied": False}


def listen():
    try:
        r = urllib.request.urlopen(API + "/events", timeout=300)
        for line in r:
            if not line:
                continue
            t = line.decode("utf-8", "replace").strip()
            if not t.startswith("data:"):
                continue
            try:
                d = json.loads(t[5:].strip())
            except Exception:
                continue
            et = d.get("type")
            data = d.get("data")
            if et:
                state["all"].append(et)
            if et == "task_started":
                state["started"] = True
            if et == "ai_final_reply":
                state["replied"] = True
            if isinstance(data, dict) and data.get("subagent_task_id"):
                state["ids"].add(data["subagent_task_id"])
                state["types"].append(data.get("subagent_type") or et)
                state["got"] = True
                print("  [SSE] subagent:", data["subagent_task_id"], data.get("subagent_type"),
                      (data.get("tool") or "")[:24], flush=True)
    except Exception as e:
        print("  [SSE] end:", str(e)[:80], flush=True)


def post_cmd(msg):
    req = urllib.request.Request(
        API + "/command", data=json.dumps({"command": msg}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def main():
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(0.5)

    # 先发一个简单任务确认 /command 能真正启动执行
    print(">> 简单任务启动测试")
    try:
        print("   send:", post_cmd("请只回复两个字：仙人掌OK"))
    except Exception as e:
        print("   send err:", e)
    for i in range(24):
        time.sleep(5)
        if state["replied"]:
            print("   简单任务在 %ds 完成（started=%s replied=%s）" % ((i + 1) * 5, state["started"], state["replied"]))
            break
        if i % 4 == 0:
            print("   等待简单任务 %ds started=%s" % ((i + 1) * 5, state["started"]))

    # 再发强制并行调研任务，抓子代理
    print(">> 并行子代理调研任务")
    state["got"] = False
    state["ids"] = set()
    state["types"] = []
    msg = ("请用浏览器分头调研以下 3 个主题，每个主题开一个独立子代理窗口并行处理："
           "① 2026 年最值得关注的 3 个开源 AI Agent 框架；② 主流向量数据库对比；"
           "③ RAG 与长上下文方案的取舍。调研完汇总成笔记发我。")
    try:
        print("   send:", post_cmd(msg))
    except Exception as e:
        print("   send err:", e)
    for i in range(40):
        time.sleep(5)
        if state["got"]:
            print("   >>> 子代理事件在 %ds 命中" % ((i + 1) * 5))
            break
        if i % 6 == 0:
            print("   等待子代理 %ds started=%s sub=%s" % ((i + 1) * 5, state["started"], state["got"]))

    time.sleep(2)
    res = {
        "subagent_task_ids": sorted(state["ids"]),
        "subagent_event_types": state["types"],
        "all_event_types": sorted(set(state["all"])),
        "subagent_detected": state["got"],
        "task_started": state["started"],
        "ai_replied": state["replied"],
    }
    print("RESULT:", json.dumps(res, ensure_ascii=False, indent=2), flush=True)
    open(r"D:\软件\XianRenZhangAgent\_subagent_live_result.json", "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
