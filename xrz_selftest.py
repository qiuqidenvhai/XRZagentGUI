"""
xrz_selftest.py — 仙人掌 Agent 端到端 GUI 测试主控

设计目标：
- 真·GUI 交互测试：通过 CDP (Chrome DevTools Protocol) 驱动 WebEngine 的真实 DOM，
  不走 /command 后门（除非显式说明）。点 📎、拖拽、点 ▶ 都走真实 DOM 事件。
- 监视后端子代理系统找 bug：全程订阅 /events SSE 流，全量落盘 + 自动标记异常。
- 不干扰用户：物理鼠标/键盘零占用，截图走 win32 PrintWindow（窗口最小化也能抓）。

调用方式：运行测试.bat 双击即跑；或在命令行：
    D:\\软件\\Python\\python.exe xrz_selftest.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import websocket  # websocket-client
except ImportError:
    print("缺少 websocket-client，请先运行：D:\\软件\\Python\\python.exe -m pip install websocket-client",
          file=sys.stderr)
    sys.exit(2)

# ── 配置 ──────────────────────────────────────────────────────────────
APP_HOST = "127.0.0.1"
APP_PORT = 8888              # terminal.py HTTP 服务端口
CDP_PORT = 9222              # QtWebEngine 远程调试端口（启动仙人掌.bat 已设置）
TEST_ROOT = Path(r"C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test")
TEST_ROOT.mkdir(parents=True, exist_ok=True)

EVENT_TIMEOUT_S = 180        # 单任务等 ai_final_reply 的最长时间
THINK_TIMEOUT_S = 60         # 等思考块出现


# ── 工具：写一个最小的合法 PDF（1 页，含一行文本） ───────────────────
def make_minimal_pdf(out: Path, text: str = "Hello PDF") -> int:
    """手搓最小合法 PDF，避免引入 reportlab 等额外依赖。"""
    content_stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objects.append(b"<< /Length " + str(len(content_stream)).encode("ascii") + b" >>\nstream\n"
                   + content_stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode("ascii"))
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\n".encode("ascii"))
    buf.write(f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    out.write_bytes(buf.getvalue())
    return out.stat().st_size


# ── 后端 HTTP（urllib，不引依赖） ────────────────────────────────────
class Backend:
    """仙人掌 Agent 的 HTTP 控制面封装。"""

    def __init__(self, host: str = APP_HOST, port: int = APP_PORT):
        self.base = f"http://{host}:{port}"

    def _req(self, method: str, path: str, body: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: float = 10.0) -> tuple[int, Any]:
        url = self.base + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                try:
                    return r.status, json.loads(raw.decode("utf-8"))
                except Exception:
                    return r.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, str(e)

    def health(self) -> dict:
        s, d = self._req("GET", "/health")
        return d if isinstance(d, dict) else {}

    def platforms(self) -> list:
        s, d = self._req("GET", "/platforms")
        if isinstance(d, dict):
            return d.get("platforms", [])
        return []

    def switch_platform(self, key: str) -> dict:
        s, d = self._req("POST", "/platform", {"platform": key}, timeout=130.0)
        return d if isinstance(d, dict) else {"type": "error", "text": str(d), "status": s}

    def post_command(self, text: str, attachments: Optional[list] = None) -> dict:
        body = {"command": text, "attachments": attachments or []}
        s, d = self._req("POST", "/command", body, timeout=10.0)
        return d if isinstance(d, dict) else {"type": "error", "text": str(d), "status": s}


# ── CDP 客户端（直连 WebEngine 远程调试） ─────────────────────────────
class CDPClient:
    """最小 CDP 客户端：attach 到 WebEngine 的第一个 page target，支持
    Runtime.evaluate 与 Page.captureScreenshot。"""

    def __init__(self, host: str = "127.0.0.1", port: int = CDP_PORT, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ws: Optional[websocket.WebSocket] = None
        self._id = 0
        self._futures: dict[int, "asyncio.Future"] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._console_buf: list[dict] = []
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        # 1) 拿 target list
        try:
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/json",
                                        timeout=self.timeout) as r:
                targets = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"CDP /json 拉取失败：{e}（确认启动仙人掌.bat 已设置 QTWEBENGINE_REMOTE_DEBUGGING=9222）")
        page = None
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                page = t
                break
        if not page:
            raise RuntimeError(f"CDP 没找到 page target：targets={targets[:3]}...")
        ws_url = page["webSocketDebuggerUrl"]

        # 2) 建 WebSocket（websocket-client 是同步库，包到 executor）
        loop = asyncio.get_running_loop()
        def _open():
            return websocket.create_connection(ws_url, timeout=self.timeout,
                                               enable_multithread=True)
        self.ws = await loop.run_in_executor(None, _open)

        # 3) 开 Runtime 域，订阅 console
        await self._send("Runtime.enable")
        await self._send("Page.enable")

        # 4) 起接收协程
        self._recv_task = loop.create_task(self._recv_loop())

    async def close(self):
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except Exception:
                pass
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    async def _send(self, method: str, params: Optional[dict] = None) -> int:
        async with self._lock:
            self._id += 1
            mid = self._id
        msg = json.dumps({"id": mid, "method": method, "params": params or {}})
        loop = asyncio.get_running_loop()
        def _w():
            self.ws.send(msg)
        await loop.run_in_executor(None, _w)
        return mid

    async def _recv_loop(self):
        loop = asyncio.get_running_loop()
        while True:
            data = await loop.run_in_executor(None, self.ws.recv)
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if "id" in msg:
                mid = msg["id"]
                fut = self._futures.pop(mid, None)
                if fut and not fut.done():
                    fut.get_loop().call_soon_threadsafe(fut.set_result, msg)
            elif msg.get("method") == "Runtime.consoleAPICalled":
                try:
                    params = msg.get("params", {})
                    args = params.get("args", [])
                    text = " ".join(
                        str(a.get("value", a.get("description", ""))) for a in args
                    )
                    self._console_buf.append({
                        "type": params.get("type"),
                        "text": text[:500],
                        "ts": time.time(),
                    })
                    if len(self._console_buf) > 200:
                        del self._console_buf[:100]
                except Exception:
                    pass
            elif msg.get("method") == "Runtime.exceptionThrown":
                try:
                    self._console_buf.append({
                        "type": "exception",
                        "text": msg.get("params", {}).get("exceptionDetails", {}).get("text", ""),
                        "ts": time.time(),
                    })
                except Exception:
                    pass

    async def _call(self, method: str, params: Optional[dict] = None,
                    timeout: float = 10.0) -> dict:
        mid = await self._send(method, params)
        fut = asyncio.get_running_loop().create_future()
        self._futures[mid] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._futures.pop(mid, None)
            return {"error": "timeout", "method": method}

    async def evaluate(self, expr: str, timeout: float = 10.0) -> Any:
        r = await self._call("Runtime.evaluate", {"expression": expr,
                                                  "returnByValue": True,
                                                  "awaitPromise": True}, timeout=timeout)
        if "error" in r:
            return {"__cdp_error__": r["error"]}
        result = r.get("result", {})
        if "exceptionDetails" in result:
            return {"__cdp_exception__": result["exceptionDetails"].get("text", "")}
        return result.get("result", {}).get("value")

    async def set_files(self, files: list[str], selector: str = "#fileInput",
                        timeout: float = 10.0) -> dict:
        """用 DOM.setFileInputFiles 触发真实文件选择器（弹 onFilePicked，不弹系统对话框）。"""
        # 先查 nodeId
        doc = await self._call("DOM.getDocument", timeout=timeout)
        root = doc.get("result", {}).get("root", {}).get("nodeId")
        if not root:
            return {"error": "no root"}
        q = await self._call("DOM.querySelector", {"nodeId": root, "selector": selector}, timeout=timeout)
        nid = q.get("result", {}).get("nodeId")
        if not nid:
            return {"error": f"selector not found: {selector}"}
        return await self._call("DOM.setFileInputFiles",
                                {"nodeId": nid, "files": files}, timeout=timeout)

    async def click(self, selector: str, timeout: float = 5.0) -> dict:
        """用 JS 真实点一个元素（等同 mouse.click），触发 onclick 等真实事件链。"""
        return await self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f"  if (!el) return false; el.click(); return true; }})()",
            timeout=timeout,
        )

    async def screenshot_png(self, full_page: bool = False) -> bytes:
        r = await self._call("Page.captureScreenshot",
                             {"format": "png",
                              "captureBeyondViewport": full_page}, timeout=15.0)
        data = r.get("result", {}).get("data")
        if not data:
            return b""
        import base64
        return base64.b64decode(data)

    def console_snapshot(self) -> list[dict]:
        snap = list(self._console_buf)
        self._console_buf.clear()
        return snap


# ── SSE 订阅（后台线程 → 落盘 + 回调） ───────────────────────────────
class SSESubscriber:
    """订阅 /events 流，把事件落 events.jsonl + 通知回调。"""

    def __init__(self, backend: Backend, log_path: Path,
                 on_event: Optional[callable] = None):
        self.backend = backend
        self.log_path = log_path
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def start(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists():
            self.log_path.unlink()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self):
        import http.client
        while not self._stop.is_set():
            try:
                conn = http.client.HTTPConnection(APP_HOST, APP_PORT, timeout=60)
                conn.request("GET", "/events")
                resp = conn.getresponse()
                if resp.status != 200:
                    time.sleep(2.0)
                    continue
                buf = b""
                while not self._stop.is_set():
                    chunk = resp.read(1)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n\n" in buf:
                        raw, buf = buf.split(b"\n\n", 1)
                        for line in raw.split(b"\n"):
                            if line.startswith(b"data: "):
                                payload = line[6:].decode("utf-8", "replace").strip()
                                try:
                                    e = json.loads(payload)
                                except Exception:
                                    continue
                                self._dispatch(e)
            except Exception as e:
                if not self._stop.is_set():
                    print(f"[SSE] 连接中断: {e}，2s 后重连", flush=True)
                    time.sleep(2.0)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _dispatch(self, e: dict):
        with self._lock:
            self._events.append(e)
        # 落盘（每行 JSON）
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if self.on_event:
            try:
                self.on_event(e)
            except Exception:
                pass

    def events_after(self, marker_index: int) -> list[dict]:
        with self._lock:
            return list(self._events[marker_index:])

    def count(self) -> int:
        with self._lock:
            return len(self._events)


# ── 单个测试任务 ─────────────────────────────────────────────────────
@dataclass
class TaskResult:
    name: str
    platform: str
    passed: bool
    notes: list[str] = field(default_factory=list)
    bugs: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


# ── 任务编排 ─────────────────────────────────────────────────────────
class Runner:
    def __init__(self):
        self.backend = Backend()
        self.sse = SSESubscriber(self.backend, TEST_ROOT / "events.jsonl")
        self.cdp = CDPClient()
        self.results: list[TaskResult] = []
        self._console_dump: list[dict] = []

    # ── 流程辅助 ──
    async def _probe_app(self) -> bool:
        h = self.backend.health()
        return bool(h.get("status") == "ok")

    async def _attach(self):
        # 后端 + CDP + SSE
        await self.cdp.connect()
        self.sse.start()

    async def _detach(self):
        self.sse.stop()
        await self.cdp.close()

    async def _wait_thinking_block(self, timeout: float = THINK_TIMEOUT_S) -> dict:
        """等 DOM 里出现「🧠 思考过程」块（实时观察 AI 计划）。"""
        expr = """
        (() => {
          const blocks = document.querySelectorAll('.thinking-block');
          if (!blocks.length) return null;
          const last = blocks[blocks.length - 1];
          const body = last.querySelector('.think-body') || last;
          const txt = (body.innerText || '').trim();
          return { has: true, text: txt.slice(0, 1000), collapsed: last.classList.contains('collapsed') };
        })()
        """
        deadline = time.time() + timeout
        last_seen = None
        while time.time() < deadline:
            r = await self.cdp.evaluate(expr)
            if r and r.get("text"):
                return r
            await asyncio.sleep(0.5)
        return {"has": False, "text": "", "timeout": True}

    async def _shot(self, out: Path, full: bool = False) -> None:
        png = await self.cdp.screenshot_png(full_page=full)
        if png:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)

    async def _send_via_dom(self, text: str) -> None:
        """真·GUI：把文字写入 #input，触发 input 事件，再点 ▶。"""
        # 1) set value + dispatch input event
        ok = await self.cdp.evaluate(
            f"""
            (() => {{
              const el = document.getElementById('input');
              if (!el) return 'no_input';
              el.focus();
              el.value = {json.dumps(text)};
              el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              return 'ok';
            }})()
            """
        )
        if ok != "ok":
            raise RuntimeError(f"DOM 写入 input 失败: {ok}")
        # 2) 点 sendBtn（触发 onclick="sendInput()"）
        clicked = await self.cdp.click("#sendBtn")
        if not clicked:
            raise RuntimeError("DOM 点 sendBtn 失败")

    async def _send_with_attachment(self, text: str, file_path: str) -> None:
        """真·GUI：先 DOM.setFileInputFiles 触发 onFilePicked → 文件 chip 出现，再写文字 + 点 ▶。"""
        # 1) 设文件
        r = await self.cdp.set_files([file_path], "#fileInput")
        if r.get("error"):
            raise RuntimeError(f"DOM.setFileInputFiles 失败: {r}")
        await asyncio.sleep(1.0)  # 等 onFilePicked → uploadFile → pendingAttachments
        # 2) 写文字 + 点发送
        await self._send_via_dom(text)

    async def _wait_ai_final_reply(self, marker_index: int, timeout: float = EVENT_TIMEOUT_S) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            tail = self.sse.events_after(marker_index)
            for e in tail:
                t = e.get("type", "")
                if t == "ai_final_reply":
                    return e
                if t in ("command_error",) or t.endswith("_error"):
                    return e
            await asyncio.sleep(0.5)
        return {"type": "timeout", "ts": time.time()}

    async def _wait_thinking_event(self, marker_index: int, timeout: float = THINK_TIMEOUT_S) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            tail = self.sse.events_after(marker_index)
            for e in tail:
                t = e.get("type", "")
                if t in ("ai_thinking", "THINKING"):
                    return True
            await asyncio.sleep(0.4)
        return False

    # ── 单个任务模板 ──
    async def _run_task(self, name: str, platform: str, prompt: str,
                        attachment: Optional[str] = None,
                        check_files: Optional[list] = None,
                        check_keywords: Optional[list] = None,
                        expect_thinking: bool = True) -> TaskResult:
        res = TaskResult(name=name, platform=platform, passed=False,
                         artifacts={"shots": []})
        print(f"\n=== {name} | {platform} ===", flush=True)

        # 1) 切平台
        marker_before_switch = self.sse.count()
        sw = self.backend.switch_platform(platform)
        res.notes.append(f"switch: {sw}")
        if sw.get("type") == "error":
            res.bugs.append(f"平台切换失败: {sw.get('text')}")
            self.results.append(res)
            return res
        # 确认 /health.platform 正确
        h = self.backend.health()
        if h.get("platform") != platform:
            res.bugs.append(f"切换后 /health.platform={h.get('platform')!r}，期望 {platform!r}")
            self.results.append(res)
            return res
        await asyncio.sleep(0.6)

        # 2) 起始截图
        shot_pre = TEST_ROOT / name / "shots" / "01_before.png"
        await self._shot(shot_pre)
        res.artifacts["shots"].append(str(shot_pre))

        # 3) 真·GUI 发消息
        marker = self.sse.count()
        try:
            if attachment:
                await self._send_with_attachment(prompt, attachment)
            else:
                await self._send_via_dom(prompt)
        except Exception as e:
            res.bugs.append(f"GUI 发送失败: {e}")
            self.results.append(res)
            return res

        # 4) 等后端响应 + 思考块
        final = await self._wait_ai_final_reply(marker, timeout=EVENT_TIMEOUT_S)
        tname = final.get("type", "?")
        res.artifacts["ai_final_reply_type"] = tname
        if tname == "timeout":
            res.bugs.append(f"超时 {EVENT_TIMEOUT_S}s 没等到 ai_final_reply")
        elif tname == "command_error":
            res.bugs.append(f"后端 command_error: {final.get('data', {}).get('text', '')}")
        else:
            data = final.get("data", {}) or {}
            res.notes.append(f"ai_final_reply: {(data.get('text') or '')[:120]!r}")

        # 5) 思考块验证（DOM 截图佐证）
        think = await self._wait_thinking_block()
        res.artifacts["thinking_block"] = think
        if expect_thinking and not think.get("has"):
            res.bugs.append("DOM 没找到 🧠 思考过程 块")
        elif expect_thinking:
            shot_think = TEST_ROOT / name / "shots" / "02_thinking.png"
            await self._shot(shot_think)
            res.artifacts["shots"].append(str(shot_think))

        # 6) 思考事件验证（SSE）
        res.artifacts["thinking_event_seen"] = await self._wait_thinking_event(marker, timeout=8.0)

        # 7) 终态截图
        shot_post = TEST_ROOT / name / "shots" / "03_after.png"
        await self._shot(shot_post)
        res.artifacts["shots"].append(str(shot_post))

        # 8) 文件检查
        if check_files:
            ok = True
            for p in check_files:
                pp = Path(p)
                if not pp.exists():
                    res.bugs.append(f"期望产物缺失: {pp}")
                    ok = False
                elif pp.stat().st_size == 0:
                    res.bugs.append(f"产物空: {pp}")
                    ok = False
            res.artifacts["files_ok"] = ok
        if check_keywords:
            blob = " ".join(str(e.get("data", {})) for e in self.sse.events_after(marker))
            for kw in check_keywords:
                if kw not in blob:
                    res.bugs.append(f"回复里没出现关键词 {kw!r}")

        # 9) 收 console + SSE bug 标记
        cons = self.cdp.console_snapshot()
        if cons:
            errs = [c for c in cons if c.get("type") in ("error", "exception")]
            if errs:
                res.bugs.append(f"页面 console 报错 {len(errs)} 条（首条: {errs[0].get('text')[:120]}）")
            res.artifacts["console_count"] = len(cons)
        tail_events = self.sse.events_after(marker_before_switch)
        error_events = [e for e in tail_events if e.get("type", "").endswith("_error")]
        if error_events:
            res.bugs.append(f"后端发了 {len(error_events)} 条 error 事件")

        # 10) 判过
        res.passed = (not res.bugs) and (tname == "ai_final_reply")
        self.results.append(res)
        print(f"  -> {'PASS' if res.passed else 'FAIL'}  bugs={len(res.bugs)}  notes={len(res.notes)}", flush=True)
        return res

    # ── 入口 ──
    async def run(self, only: Optional[list[str]] = None):
        # 准备样本
        samples = TEST_ROOT / "samples"
        samples.mkdir(parents=True, exist_ok=True)
        pdf = samples / "sample.pdf"
        note = samples / "note.txt"
        if not pdf.exists():
            make_minimal_pdf(pdf, "仙人掌 PDF 测试样本 —— 这是用于验证文件上传/摘要功能的合成 PDF。")
        if not note.exists():
            note.write_text("这是 note.txt 原始内容。\n第一行说明。\n第二行占位。\n",
                            encoding="utf-8")

        # 探活
        if not await self._probe_app():
            print("[致命] /health 探不活：仙人掌 Agent 没开。请先双击「启动仙人掌.bat」。",
                  file=sys.stderr, flush=True)
            return

        try:
            await self._attach()
        except Exception as e:
            print(f"[致命] CDP attach 失败：{e}", file=sys.stderr, flush=True)
            return

        # 任务集
        tasks = []
        # T1: DeepSeek 生成 Word 报告
        if not only or "T1" in only:
            tasks.append(("T1_deepseek_word_report", "deepseek",
                          f"生成一份 Word 报告《仙人掌 Agent 自测报告》，包含 3 个小节"
                          f"（概述、本次测试、结论），保存到 {TEST_ROOT / 'report.docx'}。",
                          None,
                          [str(TEST_ROOT / "report.docx")],
                          ["desktop"], True))
        # T2: DeepSeek 上传 PDF 摘要
        if not only or "T2" in only:
            tasks.append(("T2_deepseek_pdf_summary", "deepseek",
                          f"读取附件 {samples / 'sample.pdf'} 的内容，写一份约 200 字的中文摘要，回复给我。",
                          str(samples / "sample.pdf"),
                          None, ["摘要"], True))
        # T3: Qwen 改文件
        if not only or "T3" in only:
            tasks.append(("T3_qwen_edit_note", "tongyi",
                          f"用 file_edit 工具读取 {samples / 'note.txt'}，在文件末尾追加一行：TEST_OK_仙人掌自测。",
                          None, None, None, True))
        # T4: Qwen 多轮
        if not only or "T4" in only:
            tasks.append(("T4_qwen_multiturn_a", "tongyi",
                          "请用一句话解释一下 TCP 三次握手。", None, None, ["SYN"], True))
        # T5: 思考块截图
        if not only or "T5" in only:
            tasks.append(("T5_qwen_thinking_visible", "tongyi",
                          "把 1+1=2 这件事，分 4 步详细推理给我看，每步之间用空行隔开。",
                          None, None, None, True))

        for t in tasks:
            name, plat, prompt, att, check_files, check_kw, expect_think = t
            try:
                await self._run_task(name, plat, prompt, att, check_files, check_kw, expect_think)
            except Exception as e:
                self.results.append(TaskResult(name=name, platform=plat, passed=False,
                                               bugs=[f"任务异常: {e}\n{traceback.format_exc()[-400:]}"]))

        await self._detach()

        # 总报告
        self._write_report()

    def _write_report(self):
        report = TEST_ROOT / "report.md"
        lines = ["# 仙人掌 Agent 端到端 GUI 自测报告\n",
                 f"运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                 f"环境：APP={APP_HOST}:{APP_PORT}  CDP={CDP_PORT}\n\n",
                 f"## 概览\n\n"]
        n_pass = sum(1 for r in self.results if r.passed)
        n_total = len(self.results)
        lines.append(f"- 通过：**{n_pass}/{n_total}**\n")
        # 汇总表
        lines.append("\n## 任务清单\n\n| # | 平台 | 通过 | bugs | 备注 |\n|---|---|---|---|---|\n")
        for i, r in enumerate(self.results, 1):
            lines.append(f"| {i} | {r.platform} | {'✅' if r.passed else '❌'} | "
                         f"{len(r.bugs)} | {(r.notes[0] if r.notes else '')[:40]} |\n")
        # 详情
        lines.append("\n## 详情\n")
        for r in self.results:
            lines.append(f"\n### {r.name} ({r.platform}) — {'✅ PASS' if r.passed else '❌ FAIL'}\n")
            if r.bugs:
                lines.append("**发现 bug：**\n")
                for b in r.bugs:
                    lines.append(f"- {b}\n")
            if r.notes:
                lines.append("\n**备注：**\n")
                for n in r.notes:
                    lines.append(f"- {n}\n")
            if r.artifacts.get("shots"):
                lines.append(f"\n截图：`{'`, `'.join(r.artifacts['shots'])}`\n")
        report.write_text("".join(lines), encoding="utf-8")
        print(f"\n=== 总报告：{report} ===", flush=True)
        print(f"    通过 {n_pass}/{n_total}", flush=True)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只跑指定任务编号（逗号分隔，如 T1,T3）", default=None)
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    r = Runner()
    try:
        asyncio.run(r.run(only=only))
    except KeyboardInterrupt:
        print("[中断]", flush=True)


if __name__ == "__main__":
    main()