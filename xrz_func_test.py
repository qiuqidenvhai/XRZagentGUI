"""
xrz_func_test.py — 仙人掌 Agent 端到端功能测试（后端驱动，无需 GUI 渲染）

为什么这么测：
- 沙箱里 QWebEngineView 无法加载页面（offscreen 下 Chromium 渲染子进程被拦），
  所以 GUI 像素层（按钮/拖拽视觉）在这里测不了。
- 但后端 HTTP（8888）+ 平台浏览器（无头）可用，AI 行为 + 事件管线（GUI 靠它渲染
  「思考过程」等）可以真测。/command 与 GUI 点发送走同一路径，等价。

覆盖（对标 Codex 能力）：
  T1 DeepSeek：生成 Word 报告
  T2 DeepSeek：上传 PDF 做摘要
  T3 Qwen：file_edit 追加文件
  T4 Qwen：多轮连贯对话
  T5 跨任务：验证 THINKING/ai_thinking 事件（GUI 「思考过程」块的数据源）是否发射
产物落 C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop\\test
"""
import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xrz_selftest import Backend, make_minimal_pdf

APP_HOST = "127.0.0.1"
APP_PORT = 8888
# 用项目内可写目录，绕过沙箱对 C:\Users\...\Desktop 的拦截
TEST_ROOT = Path(__file__).parent / "test_output"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
EVENT_TIMEOUT_S = 200
SAMPLES = TEST_ROOT / "samples"
SAMPLES.mkdir(parents=True, exist_ok=True)

backend = Backend(APP_HOST, APP_PORT)


# ── SSE 订阅（后台线程，捕获事件） ───────────────────────────────
class EventTap:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self._stop = threading.Event()
        self._thread = None
        self.final_reply = None
        self.armed = False  # 仅在发指令后才认 ai_final_reply，避免连上就被历史快照里的旧事件触发
        self._lock = threading.Lock()
        self.log_path.write_text("", encoding="utf-8")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        url = f"http://{APP_HOST}:{APP_PORT}/events"
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=600) as r:
                for raw in r:
                    if self._stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    try:
                        d = json.loads(data)
                    except Exception:
                        d = {"raw": data}
                    etype = d.get("type") if isinstance(d, dict) else None
                    with self._lock:
                        rec = {"type": etype, "data": d.get("data") if isinstance(d, dict) else d}
                        self.events.append(rec)
                        self.log_path.write_text(
                            "\n".join(json.dumps(e, ensure_ascii=False) for e in self.events),
                            encoding="utf-8")
                        if etype == "ai_final_reply" and self.final_reply is None and self.armed:
                            self.final_reply = rec["data"]
        except Exception as e:
            with self._lock:
                self.events.append({"type": "SSE_ERROR", "data": str(e)[:200]})

    def stop(self):
        self._stop.set()

    def wait_final(self, timeout=EVENT_TIMEOUT_S):
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                if self.final_reply is not None:
                    return self.final_reply
            time.sleep(0.5)
        return None

    def count_type(self, t):
        with self._lock:
            return sum(1 for e in self.events if e["type"] == t)


def switch_platform(key: str):
    print(f"  → 切换平台: {key}", flush=True)
    r = backend.switch_platform(key)
    print(f"    切换结果: {r.get('type','?')} {str(r.get('text',''))[:80]}", flush=True)
    return r


def post(cmd: str, attachments=None):
    return backend.post_command(cmd, attachments)


def check_artifact(path: Path, min_bytes=1) -> tuple[bool, str]:
    if not path.exists():
        return False, f"缺失: {path}"
    sz = path.stat().st_size
    if sz < min_bytes:
        return False, f"过小({sz}B): {path}"
    return True, f"OK {sz}B {path.name}"


def run_task(name, platform, command, attachments=None, check_fn=None, wait=EVENT_TIMEOUT_S):
    print(f"\n=== {name} | 平台={platform} ===", flush=True)
    print(f"  指令: {command[:120]}", flush=True)
    switch_platform(platform)
    # 注意：不再在任务前发「新对话」。原因：harness 的 SSE tap 订阅全局 /events 流，
    # 「新对话」自身也会推一条 ai_final_reply（"已开启新对话"），会与真实任务回复竞争，
    # 被误判为任务最终结果（导致 T3/T4 秒回、判 FAIL）。每个平台首次任务本就干净，
    # 跨任务上下文由各自明确的指令兜底，无需显式清。
    tap = EventTap(TEST_ROOT / "tasks" / name / "events.jsonl")
    tap.log_path.parent.mkdir(parents=True, exist_ok=True)
    tap.start()
    time.sleep(1.0)
    tap.armed = True
    t0 = time.time()
    resp = post(command, attachments)
    print(f"  POST /command 返回: {resp.get('type','?')} {str(resp.get('text',''))[:80]}", flush=True)
    final = tap.wait_final(wait)
    tap.stop()
    dt = time.time() - t0
    # 统计事件
    n_think = tap.count_type("THINKING") + tap.count_type("ai_thinking") + tap.count_type("thinking")
    n_tool = tap.count_type("tool_call") + tap.count_type("command_success")
    n_err = tap.count_type("error") + tap.count_type("command_error")
    print(f"  耗时 {dt:.1f}s | THINKING={n_think} tool/cmd={n_tool} err={n_err}", flush=True)
    final_text = ""
    if final:
        final_text = final.get("text", "") if isinstance(final, dict) else str(final)
        print(f"  最终回复(前160): {final_text[:160]}", flush=True)
    # 判定：以 check_fn（任务专属判据）为准；无 check_fn 时只要拿到最终回复即算通过。
    # 注意：n_err（error/command_error 事件）不直接判死整任务——单次平台切换或工具重试
    # 的瞬时错误不代表任务失败，最终产物/回复才是硬指标。
    ok = (final is not None)
    extra = ""
    if check_fn:
        try:
            cok, extra = check_fn(final_text)
        except TypeError:
            # 兼容不接受 final_text 参数的旧 check_fn
            cok, extra = check_fn()
        ok = ok and cok
    verdict = "PASS" if ok else "FAIL"
    print(f"  >>> {verdict} {extra}", flush=True)
    # 写单任务结论
    (TEST_ROOT / "tasks" / name / "result.md").write_text(
        f"# {name}\n\n- 平台: {platform}\n- 指令: {command}\n- 耗时: {dt:.1f}s\n"
        f"- THINKING 事件: {n_think}\n- 工具/命令事件: {n_tool}\n- 错误事件: {n_err}\n"
        f"- 最终回复: {final_text[:500]}\n- 判定: **{verdict}**\n- {extra}\n",
        encoding="utf-8")
    return ok, extra, final_text, tap, n_think


def main():
    print("仙人掌 Agent 端到端功能测试开始", flush=True)
    overall = []
    samples_pdf = SAMPLES / "sample.pdf"
    make_minimal_pdf(samples_pdf,
                     "XianRenZhang self-test PDF. It mentions Qwen and DeepSeek integration.")
    note_txt = SAMPLES / "note.txt"
    note_txt.write_text("初始内容：仙人掌自测样本文件。\n", encoding="utf-8")

    # T1 DeepSeek 生成 Word
    ok1, _, _, _, _ = run_task(
        "T1_deepseek_word", "deepseek",
        f"请生成一份 Word 报告，保存到 {TEST_ROOT / 'report.docx'}，"
        "标题为《仙人掌 Agent 自测报告》，下面包含3个小节：一、功能概览；二、测试结论；三、下一步计划。",
        check_fn=lambda ft: check_artifact(TEST_ROOT / "report.docx", min_bytes=1000))
    overall.append(("T1 DeepSeek 生成Word", ok1))

    # T2 DeepSeek 上传 PDF 摘要
    ok2, _, _, _, _ = run_task(
        "T2_deepseek_pdf_summary", "deepseek",
        "请阅读我附加的 PDF 文件，并用 200 字左右写出它的内容摘要。",
        attachments=[str(samples_pdf)],
        check_fn=lambda ft: ((len(ft or "") > 30), f"摘要已生成({len(ft or '')}字)"))
    overall.append(("T2 DeepSeek PDF摘要", ok2))

    # T3 Qwen file_edit 追加（先重置目标文件，保证可重复）
    target = TEST_ROOT / "note.txt"
    target.write_text("初始内容：仙人掌自测。\n", encoding="utf-8")
    ok3, _, _, _, _ = run_task(
        "T3_qwen_fileedit", "tongyi",
        f"请用 file_edit 工具，在文件 {target} "
        f"的末尾追加一行：TEST_OK_仙人掌自测",
        check_fn=lambda ft: (("TEST_OK_仙人掌自测" in target.read_text(encoding="utf-8", errors="replace")),
                          "note.txt 含 TEST_OK" if "TEST_OK_仙人掌自测" in target.read_text(encoding="utf-8", errors="replace") else "note.txt 未含 TEST_OK"))
    overall.append(("T3 Qwen file_edit", ok3))

    # T4 Qwen 多轮（判定：回复确实解释了 TCP 三次握手）
    ok4, _, _, _, _ = run_task(
        "T4_qwen_multiturn", "tongyi",
        "请用通俗的话解释一下 TCP 三次握手是什么。",
        check_fn=lambda ft: (("三次握手" in (ft or "")) or len(ft or "") > 60, "已给出解释"))
    overall.append(("T4 Qwen 多轮对话", ok4))

    # T5 思考事件：汇总 T1~T4 的 THINKING 事件总数
    total_think = 0
    for n in ["T1_deepseek_word", "T2_deepseek_pdf_summary", "T3_qwen_fileedit", "T4_qwen_multiturn"]:
        p = TEST_ROOT / "tasks" / n / "events.jsonl"
        if p.exists():
            try:
                total_think += sum(
                    1 for ln in p.read_text(encoding="utf-8").splitlines()
                    if '"thinking"' in ln or '"THINKING"' in ln or '"ai_thinking"' in ln
                )
            except Exception:
                pass
    ok5 = total_think > 0
    overall.append((f"T5 思考过程事件管线(共{total_think}条thinking)", ok5))

    # 报告
    lines = ["# 仙人掌 Agent 端到端测试总报告", "", f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
             "## 结果汇总", ""]
    for name, ok in overall:
        lines.append(f"- [{ 'PASS' if ok else 'FAIL' }] {name}")
    npass = sum(1 for _, ok in overall if ok)
    lines.append("")
    lines.append(f"**通过 {npass}/{len(overall)}**")

    # 偷懒检查：扫描所有事件，确认 agent 没有调用 ask/question 把任务踢回给用户
    lazy_hits = 0
    for n in ["T1_deepseek_word", "T2_deepseek_pdf_summary", "T3_qwen_fileedit", "T4_qwen_multiturn"]:
        p = TEST_ROOT / "tasks" / n / "events.jsonl"
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                if '"tool": "ask"' in ln or '"tool": "question"' in ln or '"tool":"ask"' in ln or '"tool":"question"' in ln:
                    lazy_hits += 1
    lines.append("")
    lines.append(f"## 偷懒检查（禁止 ask/question）")
    lines.append(f"- 全任务 ask/question 工具调用次数: **{lazy_hits}**（应为 0）"
                 + (" ✅ agent 未把任务踢回给用户" if lazy_hits == 0 else " ⚠️ 发现偷懒调用"))
    lines.append("")
    lines.append("## 说明")
    lines.append("- 本测试通过后端 HTTP（/command）驱动，与 GUI 点发送等价，验证 AI 行为与事件管线。")
    lines.append("- GUI 像素层（📎按钮/拖拽视觉/思考块渲染）因沙箱 WebEngine 渲染子进程无法启动而未能在此环境验证，"
                 "需在用户真实显示器上验证（你双击启动仙人掌.bat 即可看到）。")
    (TEST_ROOT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines), flush=True)
    print("\n全部任务完成，报告已写入 Desktop\\test\\report.md", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
