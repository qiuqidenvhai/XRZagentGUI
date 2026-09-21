import socket, json, urllib.request, sys

# 1) 9333 bridge reachable?
try:
    s = socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", 9333)); s.close()
    print("9333 OPEN")
except Exception as e:
    print("9333 CLOSED:", e)

# 2) 8888 backend health
try:
    with urllib.request.urlopen("http://127.0.0.1:8888/health", timeout=5) as r:
        d = json.loads(r.read().decode("utf-8"))
        print("8888 health:", {k: d.get(k) for k in ("agent_ready", "platform") if k in d})
except Exception as e:
    print("8888 ERR:", e)
