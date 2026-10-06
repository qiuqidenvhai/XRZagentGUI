# -*- coding: utf-8 -*-
"""
_regress_dist.py —— 在【dist 交付包后端】上回归两块用户点名的功能：
  R1 子母代理（subagent）：强制 browser_research → 断言 SSE 冒泡出 subagent_task_id 事件
     （证明 set_event_forwarder 转发链在 dist 上生效），且后端日志无 'has no attribute profile' 崩溃。
  R2 侧边预览（artifact preview）：生成 docx → 按 conversation_id 查 /files + /attachments
     → /preview 取回正文，证明产物在侧边栏可达 + 可预览。
全部走 dist 后端 HTTP（与 GUI 点发送同路径）。产物落在 dev 源 xrz_data（不污染交付包）。
"""
import json, os, socket, sys, threading, time, urllib.request, urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:8888"
DIST = Path(r"D:/软件/XianRenZhangAgent/dist/XianRenZhangAgent")
DEV_DATA = Path(r"D:/软件/XianRenZhangAgent/xrz_data")
REGISTRY = DEV_DATA / ".xianrenzhang_agent" / "conversation_artifacts.json"
OUT = Path(r"D:/软件/XianRenZhangAgent/test_output/dist_regress")
OUT.mkdir(parents=True, exist_ok=True)
os.environ["no_proxy"] = "127.0.0.1,localhost"

events = []
ev_lock = threading.Lock()
stop_evt = threading.Event()


def sse_capture():
    try:
        s = socket.create_connection(("127.0.0.1", 8888), timeout=10)
        s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nAccept: text/event-stream\r\n\r\n")
        s.settimeout(2.0)
        buf = b""
        while not stop_evt.is_set():
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line.startswith(b"data:"):
                    payload = line[5:].strip()
                    try:
                        events.append(json.loads(payload.decode("utf-8", "replace")))
                    except Exception:
                        pass
    except Exception as e:
        with ev_lock:
            events.append({"__sse_error": str(e)})


def post(path, body, t=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def get(path, t=30):
    with urllib.request.urlopen(BASE + path, timeout=t) as r:
        return r.status, r.read().decode("utf-8", "replace")


def count_subtype(sub):
    with ev_lock:
        return sum(1 for e in events if isinstance(e, dict) and (
            (isinstance(e.get("data"), dict) and sub in e["data"]) or
            json.dumps(e, ensure_ascii=False).find(sub) >= 0))


# ── R1 子母代理 ───────────────────────────────────────────────────────
def test_subagent():
    print("\n===== R1 子母代理（dist 后端）=====", flush=True)
    print("  切换平台 tongyi", flush=True)
    sw = post("/platform", {"platform": "tongyi"}, t=150)
    print("  switch ->", sw.get("type"), flush=True)
    n_before = count_subtype("subagent_task_id")
    prompt = ("请务必调用 browser_research 工具（它会启动子代理窗口去网页上做深度研究）完成下面任务，"
              "禁止你自己直接回答或用自带联网搜索：研究一下仙人掌（多肉植物）的主要科属和 3 个常见品种，"
              "给出名字、科属、养护要点。")
    resp = post("/command", {"command": prompt}, t=30)
    print("  /command ->", str(resp)[:80], flush=True)
    deadline = time.time() + 15 * 60
    saw_sub = None
    final = None
    while time.time() < deadline:
        time.sleep(5)
        now = count_subtype("subagent_task_id")
        if now > n_before and saw_sub is None:
            saw_sub = now
        if final is None:
            for e in events:
                if isinstance(e, dict) and e.get("type") == "ai_final_reply":
                    final = e
                    break
        if saw_sub and final:
            break
    logtxt = ""
    logf = DIST / "backend_test.log"
    with ev_lock:
        sub_events = [e for e in events if isinstance(e, dict) and (
            (isinstance(e.get("data"), dict) and "subagent_task_id" in e["data"]))]
    crashed = False
    for lf in [DIST / "backend_test.log"]:
        if lf.exists():
            logtxt = lf.read_text(encoding="utf-8", errors="ignore")
            if "has no attribute 'profile'" in logtxt:
                crashed = True
    ok = (len(sub_events) > 0) and (final is not None) and (not crashed)
    print(f"  subagent_task_id 事件数: {len(sub_events)}（{'>0 通过' if sub_events else '=0 未冒泡，转发链失效'}）", flush=True)
    print(f"  后端日志 .profile 崩溃: {'有（FAIL）' if crashed else '无'}", flush=True)
    if final:
        ft = (final.get("data") or {}).get("text", "") if isinstance(final.get("data"), dict) else str(final.get("data"))
        print(f"  最终回复前160: {str(ft)[:160]}", flush=True)
    (OUT / "R1_subagent.json").write_text(json.dumps({
        "subagent_events": len(sub_events), "final": bool(final), "crashed": crashed, "ok": ok
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  >>> R1 {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


# ── R2 侧边预览（产物可达 + /preview）────────────────────────────────
def test_artifact_preview():
    print("\n===== R2 侧边预览（dist 后端）=====", flush=True)
    print("  切换平台 deepseek（生成 docx）", flush=True)
    sw = post("/platform", {"platform": "deepseek"}, t=150)
    print("  switch ->", sw.get("type"), flush=True)
    target = OUT / "dist_regress_report.docx"
    if target.exists():
        target.unlink()
    reg_before = set()
    if REGISTRY.exists():
        try:
            reg_before = set(json.loads(REGISTRY.read_text(encoding="utf-8")).keys())
        except Exception:
            reg_before = set()
    prompt = (f"请生成一份 Word 文档，保存到 {target.as_posix()}，"
              f"标题《仙人掌 Agent dist 回归报告》，含三节：一、背景；二、测试结论；三、下一步。")
    post("/command", {"command": prompt}, t=30)
    print("  已发 docx 生成任务，等待落盘...", flush=True)
    doc_ok = False
    deadline = time.time() + 12 * 60
    while time.time() < deadline:
        if target.exists() and target.stat().st_size > 1000:
            doc_ok = True
            break
        time.sleep(5)
    print(f"  docx 落盘: {'OK ' + str(target.stat().st_size) + 'B' if doc_ok else 'MISSING/过小'}", flush=True)

    # 从 registry 找该 docx 所属 conversation_id
    conv_id = None
    newest_bucket = None
    if REGISTRY.exists():
        try:
            reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
            for cid, bucket in reg.items():
                if cid in reg_before:
                    continue
                paths = bucket.get("paths") if isinstance(bucket, dict) else bucket
                paths = paths or []
                for p in paths:
                    if "dist_regress_report.docx" in str(p):
                        conv_id, newest_bucket = cid, paths
                        break
        except Exception as e:
            print("  registry 读取失败:", e, flush=True)
    print(f"  该 docx 所属 conversation_id: {conv_id}", flush=True)

    files_ok = att_ok = prev_ok = False
    if conv_id:
        # /files 与 /attachments 按会话过滤
        try:
            st, body = get(f"/files?conversation_id={conv_id}")
            blob = json.dumps(json.loads(body), ensure_ascii=False) if body.strip()[:1] in "[{" else body
            files_ok = "dist_regress_report.docx" in blob
            print(f"  /files?conv={conv_id} 含产物: {files_ok}（len={len(body)}）", flush=True)
        except Exception as e:
            print("  /files 失败:", e, flush=True)
        try:
            st, body = get(f"/attachments?conversation_id={conv_id}")
            ablob = json.dumps(json.loads(body), ensure_ascii=False) if body.strip()[:1] in "[{" else body
            att_ok = "dist_regress_report.docx" in ablob
            print(f"  /attachments?conv={conv_id} 含产物: {att_ok}（len={len(body)}）", flush=True)
        except Exception as e:
            print("  /attachments 失败:", e, flush=True)
        # /preview 取 docx 正文
        try:
            st, body = get(f"/preview?path={urllib.parse.quote(str(target))}", t=60)
            prev_ok = ("背景" in body or "测试结论" in body or "下一步" in body or len(body) > 100)
            print(f"  /preview docx 正文: {'取到' if prev_ok else '空/失败'}（{st}, len={len(body)}）", flush=True)
        except Exception as e:
            print("  /preview 失败:", e, flush=True)

    ok = doc_ok and (files_ok and att_ok and prev_ok)
    (OUT / "R2_artifact_preview.json").write_text(json.dumps({
        "doc_ok": doc_ok, "conversation_id": conv_id, "files_ok": files_ok,
        "attachments_ok": att_ok, "preview_ok": prev_ok, "ok": ok
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  >>> R2 {'PASS' if ok else 'FAIL'}（doc={doc_ok} files={files_ok} att={att_ok} preview={prev_ok}）", flush=True)
    return ok


import urllib.parse  # noqa

if __name__ == "__main__":
    th = threading.Thread(target=sse_capture, daemon=True)
    th.start()
    time.sleep(1.5)
    r1 = test_subagent()
    r2 = test_artifact_preview()
    stop_evt.set()
    print("\n===== 回归总结（dist 后端）=====")
    print(f"  R1 子母代理:   {'PASS' if r1 else 'FAIL'}")
    print(f"  R2 侧边预览:   {'PASS' if r2 else 'FAIL'}")
    print(f"  总评: {'ALL PASS' if (r1 and r2) else '有 FAIL，需定位修复'}")
