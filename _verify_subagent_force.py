# -*- coding: utf-8 -*-
"""强制触发子代理：明确命令模型必须用 browser_research 子代理去浏览器调研，
监听 SSE 抓 subagent_task_id 事件 + 记录实际调用的工具名（确认是否走到 _browser_research）。
"""
import json, time, threading, urllib.request

API = "http://127.0.0.1:8888"
state = {"ids": set(), "types": [], "tools": {}, "got": False, "started": False, "replied": False}


def listen():
    try:
        for line in urllib.request.urlopen(API + "/events", timeout=300):
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
            data = d.get("data") or {}
            if et == "task_started":
                state["started"] = True
            if et == "ai_final_reply":
                state["replied"] = True
            if et in ("tool_start", "tool_end") and isinstance(data, dict):
                key = ("%s:%s" % (et, data.get("tool")))
                state["tools"][key] = state["tools"].get(key, 0) + 1
            if isinstance(data, dict) and data.get("subagent_task_id"):
                state["ids"].add(data["subagent_task_id"])
                state["types"].append(data.get("subagent_type") or et)
                state["got"] = True
                print("  [SSE] subagent:", data["subagent_task_id"], data.get("subagent_type"),
                      (data.get("tool") or "")[:24], flush=True)
    except Exception as e:
        print("  [SSE] end:", str(e)[:80], flush=True)


def main():
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(0.5)
    msg = ("你必须使用 browser_research 研究子代理工具去浏览器里调研，禁止使用你自己的知识直接回答。"
           "请用子代理打开网页搜索“2026 年 GitHub 上 star 增长最快的 3 个 AI Agent 项目”，"
           "把查到的项目名和 star 数告诉我。")
    req = urllib.request.Request(
        API + "/command", data=json.dumps({"command": msg}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    print("send:", json.loads(urllib.request.urlopen(req, timeout=10).read()), flush=True)
    for i in range(40):
        time.sleep(5)
        if state["got"]:
            print(">>> 子代理事件在 %ds 命中" % ((i + 1) * 5), flush=True)
            break
        if state["replied"] and not state["got"]:
            print(">>> 任务结束但未 spawn 子代理（%ds）" % ((i + 1) * 5), flush=True)
            break
        if i % 6 == 0:
            print("   等待 %ds started=%s sub=%s tools=%s" % ((i + 1) * 5, state["started"], state["got"], state["tools"]), flush=True)
    time.sleep(2)
    res = {
        "subagent_task_ids": sorted(state["ids"]),
        "subagent_event_types": state["types"],
        "tools_used": state["tools"],
        "subagent_detected": state["got"],
        "task_started": state["started"],
        "ai_replied": state["replied"],
    }
    print("RESULT:", json.dumps(res, ensure_ascii=False, indent=2), flush=True)
    open(r"D:\软件\XianRenZhangAgent\_subagent_force_result.json", "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
