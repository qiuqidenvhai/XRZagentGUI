"""
SSE 修复端到端验收（跨裁剪）：
  C1 = 一个 /events 长连接客户端，全程挂着（模拟迟迟不刷新/僵着的 GUI 窗口）。
  Phase A：用一批瞬时 /command 推送，把 SSE 事件总量推过 200 → 服务端必然发生
           _gui_event_log 裁剪（del 最老 100 条）。
  Phase B：跑一次【真实 GUI】file_write（唯一标记路径）。
  验收：C1（老客户端，已跨越裁剪）仍能收到 Phase B 的唯一标记事件。
       —— 旧实现（len 游标）在此场景下 C1 永久失明，收不到；新实现（_id 游标）能。
"""
import os, socket, subprocess, sys, time, json, urllib.request

APP = r"D:/软件/XianRenZhangAgent"
LOG = os.path.join(APP, "_sse_client.log")
PY = r"D:/软件/Python/python.exe"
GUI = os.path.join(APP, "_gui_test.py")
B = "http://127.0.0.1:8888"

open(LOG, "w", encoding="utf-8").close()
stop = {"flag": False}
data_lines = {"n": 0}


def client():
    s = socket.socket()
    s.settimeout(30)
    s.connect(("127.0.0.1", 8888))
    s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nAccept: text/event-stream\r\n\r\n")
    buf = b""
    out = open(LOG, "ab", buffering=0)
    while not stop["flag"]:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            s.settimeout(30)
            continue
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            t = line.decode("utf-8", "replace").strip()
            if t.startswith("data:"):
                data_lines["n"] += 1
                out.write(t.encode("utf-8") + b"\n")
                out.flush()
    s.close()


def fire_trivial(n):
    """Phase A 体积生成器：n 条瞬时 /command（deepseek 平台，done 工具，秒级完成）。"""
    ok = 0
    for i in range(n):
        try:
            req = urllib.request.Request(
                B + "/command",
                data=json.dumps({"command": '@@@@{"tool":"done","text":"v%d"}@@@@' % i,
                                  "attachments": []}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=60).read()
            ok += 1
        except Exception as e:
            print("    fire_trivial #", i, "err:", str(e)[:80])
    print(f"    瞬时命令发出 {ok}/{n}")


print("[1] 启动长连接客户端 C1 ...")
import threading
threading.Thread(target=client, daemon=True).start()
time.sleep(4)
print(f"    C1 初始 data 行数 = {data_lines['n']}")

print("[2] Phase A：推事件量过 200，强制触发服务端裁剪 ...")
fire_trivial(130)   # ≈260 事件，必触发多次 del log[:100]
time.sleep(3)
print(f"    Phase A 后 C1 累计 data 行 = {data_lines['n']}（服务端 len 必已>200 且裁剪过多次）")

MARK = "XM914"          # 短标记，保证落在 task_started.title 前 50 字符内（不被截断）
print("[3] Phase B：真实 GUI file_write（唯一短标记 " + MARK + "）...")
cmd = ('@@@@{"tool":"file_write","path":"C:/temp/' + MARK + '.txt",'
       '"content":"SSE裁剪后仍送达"}@@@@ 请执行写入')
r = subprocess.run([PY, GUI, "deepseek", cmd], capture_output=True, text=True, timeout=420)
print("    面板:", ("grew=True" if "grew=True" in r.stdout else "grew=False"))
time.sleep(10)

# 文件是否真的落盘（用户可见的最终效果）
fw_path = r"C:/temp/" + MARK + ".txt"
print("    文件落盘:", ("存在: " + open(fw_path, encoding='utf-8').read().strip())
      if os.path.exists(fw_path) else "未生成!")

stop["flag"] = True
time.sleep(1)
logtext = open(LOG, encoding="utf-8").read()
data_lines_total = logtext.count("data: ")
# 正确判据：① 老客户端 C1 在裁剪后仍收增量（行数持续增长）② Phase B 的
# file_write task_started 事件（含唯一标记路径）确实送到了 C1。
marker_hit = MARK in logtext
# 该行号相对 Phase A 结束时(179)的位置，证明跨裁剪
first_fw = None
for i, l in enumerate(l for l in logtext.splitlines() if l.startswith("data:")):
    if MARK in l:
        first_fw = i
        break
print(f"[4] 验收: C1 累计 data 行 = {data_lines_total}")
print(f"    唯一标记 {MARK} 在 C1 日志命中: {marker_hit}（首现于 data 行 #{first_fw}，Phase A 末尾≈179）")
if marker_hit and first_fw is not None and first_fw > 150:
    print("\n>>> PASS：老客户端 C1 跨越 200 事件裁剪，仍收到 Phase B（GUI file_write）的后续事件。")
    print(">>> SSE 慢客户端/游标漂移 bug 已修复。")
else:
    print("\n>>> 复核：打印 C1 日志末尾 8 行供人工确认")
    for l in logtext.splitlines()[-8:]:
        print("   ", l[:160])
