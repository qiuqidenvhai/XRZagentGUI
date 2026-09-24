# -*- coding: utf-8 -*-
"""真端到端验证子代理：切到 doubao（登录新鲜），强制 browser_research，
抓 /events 原始流，按「命令发送时刻」把回放历史与本轮事件分开，
统计本轮是否真的出现带 subagent_task_id 的事件（含原始事件结构采样）。
"""
import json, time, socket, threading, re, urllib.request

B = "http://127.0.0.1:8888"
HOST, PORT = "127.0.0.1", 8888
PLAT = "deepseek"
TOKEN = "SUBAGENTTEST_%d" % int(time.time())

raw_lines = []          # (recv_ts, raw_text)
_lock = threading.Lock()
STARTED_AT = [0.0]


def sse_connect():
    s = socket.create_connection((HOST, PORT), timeout=15)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    s.sendall(b"GET /events HTTP/1.1\r\nHost: %s:%d\r\nAccept: text/event-stream\r\n\r\n" % (HOST.encode(), PORT))
    buf = b""
    s.settimeout(1.0)
    while True:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            continue
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf or b"\n" in buf:
            # 按行切，保留未完成的尾行
            if b"\n\n" in buf:
                seg, buf = buf.split(b"\n\n", 1)
            else:
                idx = buf.rfind(b"\n")
                if idx < 0:
                    break
                seg, buf = buf[:idx], buf[idx+1:]
            for line in seg.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                with _lock:
                    raw_lines.append((time.time(), payload.decode("utf-8", "replace")))


def post(path, obj, timeout=30):
    req = urllib.request.Request(B + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def main():
    t = threading.Thread(target=sse_connect, daemon=True)
    t.start()
    time.sleep(1.5)  # 先把回放历史接住

    print("[*] switch ->", PLAT, flush=True)
    try:
        print("    ", post("/platform", {"platform": PLAT}, timeout=150), flush=True)
    except Exception as e:
        print("    switch err:", e, flush=True)
    time.sleep(6)

    msg = ("你必须使用 browser_research 研究子代理工具去浏览器里调研，禁止使用你自己的知识直接回答。"
           "请用子代理打开网页搜索“%s 2026 年 GitHub 上 star 增长最快的 3 个 AI Agent 项目”，"
           "把查到的项目名和 star 数告诉我。" % TOKEN)
    print("[*] send command (token=%s)" % TOKEN, flush=True)
    STARTED_AT[0] = time.time()
    try:
        print("    ", post("/command", {"command": msg}, timeout=30), flush=True)
    except Exception as e:
        print("    command err:", e, flush=True)

    # 等完成（最多 300s）
    deadline = time.time() + 300
    seen_reply = False
    while time.time() < deadline:
        with _lock:
            items = list(raw_lines)
        for ts, pl in items:
            if ts < STARTED_AT[0]:
                continue
            if '"ai_final_reply"' in pl or '"type":"ai_final_reply"' in pl or 'ai_final_reply' in pl:
                seen_reply = True
        if seen_reply:
            print("[*] ai_final_reply seen, wait 3s for tail", flush=True)
            time.sleep(3)
            break
        time.sleep(3)

    # ---- 分析 ----
    with _lock:
        items = list(raw_lines)
    replay_ids = set()
    new_ids = {}      # id -> list of (idx, sample_json)
    sample_shapes = []
    for i, (ts, pl) in enumerate(items):
        m = re.search(r'"subagent_task_id"\s*:\s*"([^"]+)"', pl)
        if not m:
            continue
        sid = m.group(1)
        if ts < STARTED_AT[0]:
            replay_ids.add(sid)
        else:
            new_ids.setdefault(sid, []).append((i, pl[:400]))
        if len(sample_shapes) < 3:
            sample_shapes.append(pl[:300])

    print("\n==== RESULT ====", flush=True)
    print("replay subagent ids:", sorted(replay_ids), flush=True)
    print("NEW (post-command) subagent ids:", sorted(new_ids.keys()), flush=True)
    # 统计命令后出现的工具事件（看模型到底调了哪些工具）
    tool_counts = {}
    for ts, pl in items:
        if ts < STARTED_AT[0]:
            continue
        m = re.search(r'"type"\s*:\s*"tool_(?:start|end)"[^}]*"tool"\s*:\s*"([^"]+)"', pl)
        if m:
            tool_counts[m.group(1)] = tool_counts.get(m.group(1), 0) + 1
    print("tool_events_post_command:", tool_counts, flush=True)
    print("subagent_detected_this_run:", bool(new_ids), flush=True)
    print("\n--- sample raw event shapes (first 3) ---", flush=True)
    for sh in sample_shapes:
        print(sh, flush=True)
    # 也打印前 5 条非回放事件的原始结构，确认字段名
    print("\n--- first 5 post-command events raw ---", flush=True)
    cnt = 0
    for ts, pl in items:
        if ts < STARTED_AT[0]:
            continue
        print(pl[:200], flush=True)
        cnt += 1
        if cnt >= 5:
            break

    out = {
        "replay_subagent_ids": sorted(replay_ids),
        "new_subagent_ids": sorted(new_ids.keys()),
        "subagent_detected_this_run": bool(new_ids),
        "sample_shapes": sample_shapes,
    }
    open(r"D:\软件\XianRenZhangAgent\_subagent_doubao_result.json", "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=2))
    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()
