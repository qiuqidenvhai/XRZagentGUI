import sys, time, threading, json, traceback
sys.path.insert(0, ".")
print("start", flush=True)
try:
    from xrz_selftest import Backend
    b = Backend()
    print("health:", b.health().get("platform"), b.health().get("agent_ready"), flush=True)
    events=[]
    stop=threading.Event()
    def tap():
        import urllib.request
        try:
            req=urllib.request.Request("http://127.0.0.1:8888/events", headers={"Accept":"text/event-stream"})
            with urllib.request.urlopen(req, timeout=200) as resp:
                for raw in resp:
                    if stop.is_set(): break
                    ln=raw.decode("utf-8","replace").rstrip("\n")
                    if not ln.startswith("data:"): continue
                    try: d=json.loads(ln[5:].strip())
                    except: continue
                    et=d.get("type")
                    events.append(et)
                    if et=="ai_final_reply":
                        print("FINAL:", str(d.get("data",{}).get("text",""))[:200], flush=True); stop.set()
        except Exception as e:
            print("SSE err:", e, flush=True)
    t=threading.Thread(target=tap, daemon=True); t.start()
    time.sleep(1)
    r=b.post_command("你好，请只回复两个字：收到")
    print("post:", r.get("type"), flush=True)
    t.join(timeout=150)
    print("seen types:", events[-12:], flush=True)
    print("PROBE_DONE", flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(2)
