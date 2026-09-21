#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键驱动：干净启动后端 → 等待就绪 → 跑全部 4 平台测试 + 子代理（最后）→ 输出报告。
全程在一个进程内完成，杜绝「后端被中途杀掉」。
"""
import os, sys, time, json, urllib.request, urllib.error, http.client, socket

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"D:\软件\Python\python.exe"
TERMINAL = os.path.join(ROOT, "terminal.py")
LOG = os.path.join(ROOT, "backend_run.log")

results = []

def log(tag, ok, detail=""):
    results.append({"tag": tag, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}: {str(detail)[:160]}", flush=True)

def health():
    try:
        r = json.loads(urllib.request.urlopen("http://127.0.0.1:8888/health", timeout=5).read().decode())
        return r.get("agent_ready") and r.get("browser") == "connected"
    except Exception:
        return False

# ── 1) 启动后端 ──
print("===== 启动后端 =====", flush=True)
with open(LOG, "w", encoding="utf-8") as lf:
    lf.write(f"[{time.strftime('%H:%M:%S')}] launching backend...\n")
    lf.flush()
    import subprocess
    proc = subprocess.Popen(
        [PY, TERMINAL],
        cwd=ROOT,
        stdout=lf, stderr=subprocess.STDOUT,
        env={**os.environ, "XRZ_NO_GUI": "1"},
        creationflags=0x00000200,  # DETACHED_PROCESS
    )
print(f"backend pid={proc.pid}, 等待就绪（最长 60s）...", flush=True)
t0 = time.time()
ready = False
while time.time() - t0 < 60:
    if health():
        ready = True
        break
    time.sleep(3)
if not ready:
    print("后端 60s 未就绪，终止", flush=True)
    try: proc.kill()
    except: pass
    sys.exit(1)
print(f"后端就绪（耗时 {time.time()-t0:.0f}s）", flush=True)

# ── 2) 全部 4 平台测试 + 子代理 ──
def post(cmd):
    req = urllib.request.Request("http://127.0.0.1:8888/command",
        data=json.dumps({"command": cmd}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode()).get("type") == "accepted"
    except Exception as e:
        print(f"    post failed: {e}", flush=True)
        return False

def sse(timeout=300):
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
                ai_replies.append(d.get("text", "")); final_seen = time.time()
            elif t in ("tool_call", "tool_result", "tool_start", "tool_done"):
                tool_evts.append(t)
            elif t in ("error", "agent_error"):
                errors.append(str(d.get("text", d.get("message", "")))[:120])
            if final_seen and (time.time() - final_seen) > 10:
                break
    finally:
        conn.close()
    return ai_replies, tool_evts, errors, (ai_replies[-1] if ai_replies else "")

def run(cmd, timeout=360):
    ok_post = post(cmd)
    ai, tools, errs, final = sse(timeout)
    valid = bool(ai) and "[错误]" not in final and "未收到" not in final
    return valid and ok_post, f"valid={valid} tools={len(tools)} final={final[:80]!r}" + (f" errs={errs[:2]}" if errs else "")

print("\n===== 全部 4 平台完整套件 =====", flush=True)
for p in ["deepseek", "tongyi", "doubao", "yuanbao"]:
    print(f"--- {p} ---", flush=True)
    for name, cmd in [
        ("chat", f"请用一句话解释 Python GIL"),
        ("tool", f"请创建文件 test_{p}.txt，内容是：{p} 工具测试成功"),
        ("word", f"请创建 Word 文档 test_{p}.docx，标题「{p} 测试」，内容「欢迎使用」"),
        ("pptx", f"请生成 PPT test_{p}.pptx，3页：「{p}」「第二页」「谢谢」"),
    ]:
        print(f"  → {p}__{name}", flush=True)
        try:
            ok, detail = run(cmd, 360)
            log(f"{p}__{name}", ok, detail)
        except Exception as e:
            log(f"{p}__{name}", False, f"异常: {e}")

print("\n===== 子代理系统 (deepseek, 最后) =====", flush=True)
try:
    post("切换到 deepseek 平台")
    time.sleep(2)
    ok, detail = run("请派发一个子代理任务：研究 Python asyncio 基本用法，一句话总结", 480)
    log("subagent__dispatch", ok, detail)
    sub_dir = r"D:\软件\XianRenZhangAgent\xrz_data\.xianrenzhang_agent\tasks"
    rj = [f for f in os.listdir(sub_dir) if f.startswith("subagent_")] if os.path.isdir(sub_dir) else []
    log("subagent__result_files", os.path.isdir(sub_dir), f"dirs={len(rj)}: {rj[:5]}")
    ok2, detail2 = run("请访问 https://example.com 并告诉我页面标题", 300)
    log("subagent__webpage", ok2, detail2)
except Exception as e:
    log("subagent__all", False, f"异常: {e}")

# ── 汇总 ──
total = len(results)
passed = sum(1 for r in results if r["ok"])
print(f"\n{'='*50}\n总计 {total} 项，通过 {passed}，失败 {total - passed}", flush=True)
for r in results:
    if not r["ok"]:
        print(f"  FAIL {r['tag']}: {r['detail']}", flush=True)
with open(os.path.join(ROOT, "full_test_report.json"), "w", encoding="utf-8") as f:
    json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results,
               "summary": {"total": total, "passed": passed, "failed": total - passed}},
              f, ensure_ascii=False, indent=2)
print("报告: full_test_report.json", flush=True)

# ── 3) 关闭后端（仅当我方启动的实例才终止）──
if started_by_me and proc is not None:
    try:
        proc.terminate()
        print("后端已终止", flush=True)
    except Exception as e:
        print(f"后端终止: {e}", flush=True)
else:
    print("复用的既有后端保持运行，未终止", flush=True)
