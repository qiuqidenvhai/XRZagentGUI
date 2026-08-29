"""快速探针：对当前 backend 直接发一条 DeepSeek 指令，验证 DeepSeek 是否真的能回话。"""
import sys, os, time, json, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xrz_selftest import Backend
import urllib.request

APP_HOST = "127.0.0.1"
APP_PORT = 8888
backend = Backend(APP_HOST, APP_PORT)

events = []
lock = threading.Lock()
final_reply = {"v": None, "armed": False}
STOP = threading.Event()

def sse_run():
    url = f"http://{APP_HOST}:{APP_PORT}/events"
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=120) as r:
            for raw in r:
                if STOP.is_set():
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                try:
                    d = json.loads(data)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                et = d.get("type")
                with lock:
                    events.append({"type": et, "data": d.get("data")})
                    if et == "ai_final_reply" and final_reply["armed"] and final_reply["v"] is None:
                        final_reply["v"] = d.get("data")
    except Exception as e:
        with lock:
            events.append({"type": "SSE_ERROR", "data": str(e)[:200]})

def main():
    print(">>> 切换平台: deepseek", flush=True)
    r = backend.switch_platform("deepseek")
    print("    切换:", r.get("type"), str(r.get("text",""))[:60], flush=True)
    print(">>> 发新对话清上下文", flush=True)
    try:
        backend.post_command("新对话")
    except Exception:
        pass
    time.sleep(2)
    t = threading.Thread(target=sse_run, daemon=True)
    t.start()
    time.sleep(1.0)
    final_reply["armed"] = True
    cmd = "1+1 等于几？请直接回答数字。"
    print(">>> POST /command:", cmd, flush=True)
    resp = backend.post_command(cmd)
    print("    POST返回:", resp.get("type"), str(resp.get("text",""))[:60], flush=True)
    t0 = time.time()
    while time.time() - t0 < 90:
        with lock:
            if final_reply["v"] is not None:
                break
        time.sleep(0.5)
    STOP.set()
    with lock:
        types = {}
        for e in events:
            types[e["type"]] = types.get(e["type"], 0) + 1
        print("\n=== 事件统计 ===", types, flush=True)
        for e in events:
            if e["type"] in ("ai_final_reply", "ai_thinking", "tool_call", "error", "command_error", "SSE_ERROR"):
                print("  EVENT", e["type"], "->", str(e["data"])[:120], flush=True)
        fr = final_reply["v"]
        print("\n=== 最终回复 ===", (fr.get("text","") if isinstance(fr, dict) else fr), flush=True)
        print("DEEPSEEK_REPLIED" if fr else "DEEPSEEK_SILENT", flush=True)

if __name__ == "__main__":
    main()
