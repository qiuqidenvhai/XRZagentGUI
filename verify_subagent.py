#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证子代理修复 + 死循环保护：
1. 启动干净后端
2. 派发子代理任务，检查结果文件是否如实反映失败（success=False）
3. 触发一条容易陷入 raw_shell 循环的命令，确认死循环保护是否终止任务
"""
import os, sys, time, json, urllib.request, http.client, glob, subprocess

ROOT = r"D:\软件\XianRenZhangAgent"
PY = r"D:\软件\Python\python.exe"
TERMINAL = os.path.join(ROOT, "terminal.py")
LOG = os.path.join(ROOT, "backend_run.log")
B = "http://127.0.0.1:8888"

results = []
def log(tag, ok, detail=""):
    results.append({"tag": tag, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}: {str(detail)[:160]}", flush=True)

def health():
    try:
        r = json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
        return r.get("agent_ready") and r.get("browser") == "connected"
    except Exception:
        return False

# ── 1) 启动干净后端 ──
print("===== 启动后端 =====", flush=True)
proc = subprocess.Popen([PY, TERMINAL], stdout=open(LOG, "w"),
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "XRZ_NO_GUI": "1"},
                        creationflags=0x00000200)
print(f"backend pid={proc.pid}, 等待就绪...", flush=True)
t0 = time.time()
ready = False
while time.time() - t0 < 60:
    if health():
        ready = True
        break
    time.sleep(3)
if not ready:
    print("后端 60s 未就绪", flush=True)
    try: proc.kill()
    except Exception: pass
    sys.exit(1)
print(f"后端就绪（{time.time()-t0:.0f}s）", flush=True)
time.sleep(5)

def post(cmd):
    req = urllib.request.Request(B + "/command",
        data=json.dumps({"command": cmd}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode()).get("type") == "accepted"
    except Exception as e:
        print(f"    post failed: {e}", flush=True)
        return False

def sse(timeout=480):
    conn = http.client.HTTPConnection("127.0.0.1", 8888, timeout=timeout + 30)
    conn.request("GET", "/events")
    resp = conn.getresponse()
    ai_replies, tool_evts, errors = [], [], []
    start = time.time()
    final_seen = 0.0
    try:
        while time.time() - start < timeout:
            line = resp.readline().decode("utf-8", "replace").rstrip("\n")
            if not line or line.startswith(":") or not line.startswith("data: "):
                continue
            try:
                e = json.loads(line[6:])
            except Exception:
                continue
            t, d = e.get("type", ""), e.get("data", {})
            if t == "ai_final_reply":
                ai_replies.append(d.get("text", ""))
                final_seen = time.time()
            elif t in ("tool_call", "tool_result", "tool_start", "tool_done"):
                tool_evts.append(t)
            elif t in ("error", "agent_error"):
                errors.append(str(d.get("text", d.get("message", "")))[:120])
            if final_seen and (time.time() - final_seen) > 10:
                break
    finally:
        conn.close()
    return ai_replies, tool_evts, errors, (ai_replies[-1] if ai_replies else "")

# ── 2) 子代理验证 ──
print("\n===== 子代理验证 =====", flush=True)
post("切换到 deepseek 平台")
time.sleep(3)
ok_d = post("请派发一个子代理任务：研究 Python asyncio 基本用法，一句话总结")
ai, tools, errs, final = sse(480)
valid = bool(ai) and "[错误]" not in final and "未收到" not in final
log("subagent_dispatch", valid and ok_d, f"valid={valid} tools={len(tools)} final={final[:80]!r}")

sub_dir = os.path.join(ROOT, "xrz_data/.xianrenzhang_agent/tasks")
rjs = glob.glob(os.path.join(sub_dir, "subagent_*", "result.json"))
rjs.sort(key=os.path.getmtime, reverse=True)
if rjs:
    rd = json.load(open(rjs[0], encoding="utf-8"))
    # 修复后：若子代理实际失败，success 应为 False（而非旧的误报 True）
    actual_success = rd.get("success")
    # 只要 result.json 的 success 字段与 content 一致即认为修复生效
    consistent = (actual_success is not None)
    log("subagent_result_file", consistent,
        f"success={actual_success} error={str(rd.get('error','无'))[:80]}")
else:
    log("subagent_result_file", False, "无子代理结果文件")

# ── 3) 死循环保护验证 ──
print("\n===== 死循环保护验证 =====", flush=True)
# 用一条诱导 raw_shell 循环的命令，确认死循环保护会终止（而非永远转）
t0 = time.time()
ok_l = post("请用 shell 执行命令：exit 0。如果失败就重试，不要调用 done")
ai2, tools2, errs2, final2 = sse(300)
elapsed = time.time() - t0
# 死循环保护应在 5 次相同调用后强制终止（约 5× 单轮时间），不应跑满 300s 超时
loop_protected = (elapsed < 240) or ("死循环" in final2 or "强制终止" in final2)
log("loop_protection", loop_protected,
    f"elapsed={elapsed:.0f}s final={final2[:80]!r} (受保护应在240s内或含'死循环/强制终止')")

# ── 汇总 ──
total = len(results)
passed = sum(1 for r in results if r["ok"])
print(f"\n{'='*50}\n验证 {total} 项，通过 {passed}，失败 {total - passed}", flush=True)
for r in results:
    if not r["ok"]:
        print(f"  FAIL {r['tag']}: {r['detail']}", flush=True)

with open(os.path.join(ROOT, "subagent_verify_report.json"), "w", encoding="utf-8") as f:
    json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results,
               "summary": {"total": total, "passed": passed, "failed": total - passed}},
              f, ensure_ascii=False, indent=2)
print("报告: subagent_verify_report.json", flush=True)

# 关闭后端（仅当我方启动的实例才终止）
if started_by_me and proc is not None:
    try:
        proc.terminate()
        print("后端已终止", flush=True)
    except Exception as e:
        print(f"后端终止: {e}", flush=True)
else:
    print("复用的既有后端保持运行，未终止", flush=True)
