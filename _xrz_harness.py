#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XRZ 测试公共工具（关键修复）：
- /events 会在连接时「回放全部历史事件」→ 直接读会拿到旧任务的 ai_final_reply。
  解决：连接后先 drain（读到静默）再发命令；并用 task_started.title 关联出本次
  命令的 task_id，只接受该 task_id 的 ai_final_reply。
- 平台切换必须用 POST /platform {"platform": key}（NL「切换到 X 平台」是空操作）。
- 用 psutil 统计「DeepSeek 浏览器实例数」（按 user-data-dir 去重），证明绝不双开。
"""
import json, re, socket, time, collections
import urllib.request
import psutil

# 【沙箱环境】所有 127.0.0.1 请求必须绕过系统代理（沙箱有全局 HTTP_PROXY，
# 会把 localhost 请求劫走返回 502，导致 CDP 调试口 / 后端接口全连不上）
import urllib.request as _urlib
_urlib.install_opener(_urlib.build_opener(_urlib.ProxyHandler({})))

HOST, PORT = "127.0.0.1", 8888
B = f"http://{HOST}:{PORT}"
DEEPSEEK_MARK = r"browser_profiles\deepseek"

# ---------------- HTTP ----------------
def health():
    try:
        return json.loads(urllib.request.urlopen(B + "/health", timeout=5).read().decode())
    except Exception as e:
        return {"error": str(e)[:80]}

def alive():
    return health().get("status") == "ok"

def post_command(cmd, attachments=None):
    # 真实 GUI（gui.html）发的是 {command, attachments}，attachments 里带上传文件的
    # 绝对路径。原来这里只发 command，模型根本不知道「刚才上传的 PDF」在哪，
    # 只能靠 file_list / glob 瞎猜 → 找不到就甩锅 → commander 判定「放弃话术」
    # 连续打回重做 → 任务空转到超时。这是测试不 faithful 造成的假失败。
    body = {"command": cmd}
    if attachments:
        body["attachments"] = attachments
    req = urllib.request.Request(B + "/command",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode()).get("type") == "accepted"
    except Exception:
        return False

def switch_model(model, timeout=60):
    """POST /model {"model": ...}"""
    req = urllib.request.Request(B + "/model",
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return str(urllib.request.urlopen(req, timeout=timeout).read().decode())[:160]
    except Exception as e:
        return f"ERR:{e}"

def get_platforms(timeout=10):
    try:
        d = json.loads(urllib.request.urlopen(B + "/platforms", timeout=timeout).read().decode())
        return {p.get("key"): p for p in d.get("platforms", [])}
    except Exception:
        return {}

def switch_platform(key, timeout=150):
    req = urllib.request.Request(B + "/platform",
        data=json.dumps({"platform": key}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
        return r.get("text") or str(r)[:120]
    except Exception as e:
        return f"ERR:{e}"

# ---------------- 进程监控 ----------------
def deepseek_paths():
    paths = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        if p.info.get("name") not in ("chrome.exe", "headless_shell.exe"):
            continue
        for a in p.info.get("cmdline") or []:
            m = re.search(r"--user-data-dir=(.+)", a)
            if not m:
                continue
            d = re.sub(r"^--monitor-self-argument=", "", m.group(1).strip().strip('"'))
            if DEEPSEEK_MARK in d:
                paths.add(d.lower())
    return paths

def deepseek_count():
    return len(deepseek_paths())

def all_context_dirs():
    paths = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        if p.info.get("name") not in ("chrome.exe", "headless_shell.exe"):
            continue
        for a in p.info.get("cmdline") or []:
            m = re.search(r"--user-data-dir=(.+)", a)
            if not m:
                continue
            d = re.sub(r"^--monitor-self-argument=", "", m.group(1).strip().strip('"'))
            paths[d] = paths.get(d, 0) + 1
    return paths

# ---------------- SSE ----------------
def _open_sse(tries=5):
    """打开 SSE。后端偶发在忙时握手慢 / 连接被瞬时拒绝 —— 必须重试，
    否则整个回归会在半路 TimeoutError 崩掉（实测 doubao 段就是这样挂的）。"""
    last = None
    for i in range(tries):
        try:
            s = socket.create_connection((HOST, PORT), timeout=10)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            try:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            s.sendall(f"GET /events HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nAccept: text/event-stream\r\n\r\n".encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                ch = s.recv(4096)
                if not ch:
                    break
                buf += ch
            _, _, rest = buf.partition(b"\r\n\r\n")
            return s, rest
        except Exception as e:
            last = e
            try:
                s.close()
            except Exception:
                pass
            time.sleep(1.5)
    raise last

def _parse(chunk, carry):
    data = carry + chunk
    events = []
    while b"\n" in data:
        line, _, data = data.partition(b"\n")
        line = line.strip()
        if line.startswith(b"data: "):
            try:
                events.append(json.loads(line[6:]))
            except Exception:
                pass
    return events, data

def _norm(s):
    return re.sub(r"\s+", "", str(s or ""))[:18]

def run(cmd, timeout=180, drain_max=25, attachments=None):
    """发命令并只取其自身的回复。返回 dict。attachments 与 GUI 保持一致。"""
    s, carry = _open_sse()
    # 1) drain 历史回放：读到连续 3 次 1s 无新数据
    s.settimeout(1.0)
    idle = 0; t0 = time.time()
    while time.time() - t0 < drain_max and idle < 3:
        try:
            ch = s.recv(65536)
            if not ch:
                break
            _, carry = _parse(ch, carry)   # 丢弃回放事件
            idle = 0
        except socket.timeout:
            idle += 1
    # 2) 发命令
    posted = post_command(cmd, attachments=attachments)
    target = None
    final = ""
    tools = set()
    types = collections.Counter()
    errs = []
    deadline = time.time() + timeout
    s.settimeout(2.0)
    while time.time() < deadline:
        try:
            ch = s.recv(65536)
        except socket.timeout:
            continue
        if not ch:
            break
        evs, carry = _parse(ch, carry)
        for e in evs:
            t = e.get("type", ""); d = e.get("data", {}) or {}
            types[t] += 1
            if t == "task_started":
                if _norm(d.get("title")) == _norm(cmd):
                    target = d.get("task_id")
            elif t in ("tool_start", "tool_end"):
                if d.get("tool"):
                    tools.add(d["tool"])
            elif t == "ai_final_reply":
                tid = d.get("task_id")
                if target is None or tid == target:
                    final = d.get("text", "")
                    if final:
                        break
            elif t in ("error", "agent_error"):
                errs.append(str(d.get("text", d.get("message", "")))[:120])
        if final:
            break
    s.close()
    # 平台自身报错页 / UI 状态行不算「有效回答」——否则会出现「工具没跑但判 PASS」的假通过。
    PLATFORM_ERR = ("连接到 Qwen", "时出现问题", "An unexpected error occurred",
                    "Oops", "系统繁忙", "网络错误", "重新连接中",
                    "未收到模型回复", "该平台连续返回错误页", "正在思考", "思考中",
                    # 平台账号额度/次数耗尽（豆包实测）——不是模型回答，不能算通过
                    "平台额度耗尽", "免费额度用完", "额度已用完", "恢复为你服务")
    # 放弃话术不算有效回答（实测 DeepSeek PDF 问答：done() 里写「抱歉，我无法…如实收尾」
    # 也被当成了 PASS）—— 必须判 FAIL，逼着上层真的把任务做完。
    GIVEUP = ("我无法", "无法在本次会话", "请手动", "请你自己", "如实收尾",
              "任务已无法", "超出我的能力", "建议自行")
    valid = bool(final) and "[错误]" not in final and "未收到" not in final \
        and "任务已中止" not in final and "已自动停止" not in final \
        and not final.startswith("失败") \
        and not any(k in final for k in PLATFORM_ERR) \
        and not any(k in final for k in GIVEUP)
    return {"ok": bool(valid and posted), "final": final, "tools": tools,
            "types": types, "errs": errs, "task_id": target, "posted": posted}
