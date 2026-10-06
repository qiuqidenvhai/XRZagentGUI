# -*- coding: utf-8 -*-
"""
_retest_dist.py —— 对上一轮 T2/T4 的 FAIL 做【隔离重测】，甄别是"代码 bug"还是"测试串台/模型行为"。
- 每任务先 /new_conversation 开干净会话（消除上一任务收尾的 ai_final_reply 串台）。
- 只认【实质】最终回复：T2 要 ≥60 字且非"任务完成"；T4 要含 TCP 关键词。
- 走 dist 后端 HTTP（与 GUI 同路径）。
"""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:8888"
os.environ["no_proxy"] = "127.0.0.1,localhost"
OUT = Path(r"D:/软件/XianRenZhangAgent/test_output/dist_retest")
OUT.mkdir(parents=True, exist_ok=True)

import io


def post(path, body, t=150):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def make_pdf(p: Path, text: str):
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"]
    objs.append(b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    buf = io.BytesIO(); buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = [0]
    for i, o in enumerate(objs, 1):
        offs.append(buf.tell()); buf.write(f"{i} 0 obj\n".encode()); buf.write(o); buf.write(b"\nendobj\n")
    xref = buf.tell(); buf.write(f"xref\n0 {len(objs)+1}\n".encode()); buf.write(b"0000000000 65535 f \n")
    for off in offs[1:]: buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()); p.write_bytes(buf.getvalue())


def run_isolated(name, platform, prompt, attachments, accept_fn, timeout_s=420):
    print(f"\n===== {name} | {platform} =====", flush=True)
    sw = post("/platform", {"platform": platform})
    print("  switch ->", sw.get("type"), flush=True)
    try:
        nc = post("/new_conversation", {}, t=150)
        print("  new_conversation ->", nc.get("type"), flush=True)
    except Exception as e:
        print("  new_conversation err(可能已默认新会话):", str(e)[:80], flush=True)
    time.sleep(2.0)
    resp = post("/command", {"command": prompt, "attachments": attachments}, t=30)
    print("  /command ->", str(resp)[:70], flush=True)
    # 轮询 /last_reply（若存在）或靠 /files 产物；这里用 SSE 订阅拿 ai_final_reply
    import socket, threading, queue
    q = queue.Queue()
    def sse():
        try:
            s = socket.create_connection(("127.0.0.1", 8888), timeout=10)
            s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nAccept: text/event-stream\r\n\r\n")
            s.settimeout(5)
            started = time.time()
            buf = b""
            while time.time() - started < timeout_s:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue
                except Exception:
                    break
                if not chunk: break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1); line = line.strip()
                    if line.startswith(b"data:"):
                        try:
                            e = json.loads(line[5:].strip().decode("utf-8", "replace"))
                            if isinstance(e, dict) and e.get("type") == "ai_final_reply":
                                q.put(e)
                        except Exception:
                            pass
        except Exception as ex:
            q.put({"__err": str(ex)})
    th = threading.Thread(target=sse, daemon=True); th.start()
    t0 = time.time()
    accepted = None
    errpat = ("[错误]", "[AI 调用失败]", "任务中断（", "AI 调用失败:", "调用失败:", "需要手动过验证", "尚未登录")
    while time.time() - t0 < timeout_s:
        try:
            e = q.get_nowait()
        except Exception:
            time.sleep(1); continue
        if "__err" in e: print("  SSE err:", e["__err"]); break
        d = e.get("data"); txt = (d.get("text") if isinstance(d, dict) else str(d)) or ""
        if any(p in txt for p in errpat):
            print(f"  遇错误串(判 FAIL): {txt[:120]}"); accepted = ("FAIL", txt); break
        if accept_fn(txt):
            accepted = ("PASS", txt); break
        # 非实质中间回复继续等
    if accepted is None:
        # 兜底：等产物或标记文件
        accepted = ("TIMEOUT", "")
    dt = time.time() - t0
    verdict, txt = accepted
    print(f"  耗时 {dt:.0f}s | 判定 {verdict} | 最终:{txt[:160]}", flush=True)
    (OUT / f"{name}.json").write_text(json.dumps(
        {"name": name, "platform": platform, "verdict": verdict,
         "final": txt, "seconds": round(dt, 1)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict == "PASS"


def main():
    results = {}
    # RT-T2 DeepSeek PDF 摘要（实质：含摘要正文、非"任务完成"、≥60字）
    pdf = OUT / "sample_re.pdf"
    make_pdf(pdf, "XianRenZhang Agent dist regression PDF about cactus and DeepSeek integration.")
    results["RT_T2_pdf_summary"] = run_isolated(
        "RT_T2_pdf_summary", "deepseek",
        "请阅读我附加的 PDF 文件，用 200 字左右写出它的内容摘要，直接回复摘要正文。",
        [str(pdf)],
        accept_fn=lambda t: (len(t.strip()) >= 60) and ("任务完成" != t.strip()) and not t.strip().startswith("任务完成"))
    # RT-T4 Tongyi 多轮 TCP（实质：含三次握手/SYN/握手等关键词）
    results["RT_T4_tcp"] = run_isolated(
        "RT_T4_tcp", "tongyi",
        "请用通俗的话解释一下 TCP 三次握手是什么，至少说明 SYN 和 ACK 的作用。",
        [],
        accept_fn=lambda t: ("三次握手" in t) or ("SYN" in t.upper() and "ACK" in t.upper()))
    print("\n===== 隔离重测总结 =====")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    print("总评:", "ALL PASS" if all(results.values()) else "仍有 FAIL")


if __name__ == "__main__":
    main()
