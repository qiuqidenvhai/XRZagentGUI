"""
_sse_resilience.py —— 仙人掌 Agent 后端 SSE 事件流加固（热补丁层）。

背景（反编译 terminal.pyc 字节码确认）：
  * _gui_event_log 是模块级普通 list，_gui_emit_nowait 每次 emit 追加；
    len > 200 时 del log[:100] 裁掉最老 100 条。
  * 原 _serve_sse（GUIHandler 方法）用 last_idx = len(log) 作为每个客户端的
    位置游标：连接时定死一次。
  * 一旦中途发生裁剪，老客户端游标整体前移 100 位；而 len 又被 cap 在 ~200，
    log[last_idx:] 永远为空 → 该客户端永久收不到任何新事件（包括
    ai_final_reply）→ 面板任务卡「运行中」、发送锁死。忙会话（>200 事件）必现。
  * 另：客户端死亡后其 64KB 写缓冲填满前 wfile.write 会挂起线程（死客户端
    线程泄漏）。

本模块不改 pyc，在 terminal.py 的 exec 之后（服务已起来）做热替换：
  1) 用 EventLogProxy 替换 _gui_event_log 全局：接口与 list 完全兼容
     （append / len / 迭代 / 切片 / del 切片），但每个 entry 在 append 时注入
     单调递增的稳定 _id —— 裁剪不再影响事件标识。
  2) 把 GUIHandler._serve_sse 绑定为 _safe_serve_sse：按 _id 续传
     （新客户端全量回放当前 log；老客户端只取 _id > last 的增量），
     并对 socket 设超时，写不进即断开该客户端（前端 EventSource 3s 自动重连，
     重连时全量回放最近 200 条，不丢最终回复）。

安装方式：terminal.py 在 exec(terminal.pyc) 前调用 install(main_module)，
由后台线程轮询等待 GUIHandler / _gui_event_log 在 __main__ 命名空间出现后
一次性替换（幂等）。替换失败不影响后端正常启动。
"""

import os
import sys
import threading
import time

_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


class EventLogProxy:
    """_gui_event_log 的 drop-in 替身：list 语义 + 稳定事件 id。"""

    def __init__(self):
        self._entries = []
        self._seq = 0

    # -- list 兼容接口（pyc 里的 emit/裁剪逻辑走这里） --
    def append(self, e):
        try:
            e["_id"] = self._seq
            self._seq += 1
        except Exception:
            pass  # entry 不可变/非 dict 也不许炸
        self._entries.append(e)

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __getitem__(self, i):
        return self._entries[i]

    def __delitem__(self, i):
        del self._entries[i]

    # -- 新能力：按稳定 id 取增量 --
    def snapshot(self):
        return list(self._entries)

    def max_id(self):
        return self._seq - 1

    def tail(self, since_id, cap=200):
        """取 _id > since_id 的事件（最多 cap 条，取最新的 cap 条）。"""
        out = [e for e in self._entries if e.get("_id", -1) > since_id]
        if len(out) > cap:
            out = out[-cap:]
        return out


def _make_safe_serve_sse(main_module, app_dir):
    """构造新的 _serve_sse(self) 方法（绑定到 GUIHandler 上）。"""

    def _safe_serve_sse(self):
        log = main_module.__dict__.get("_gui_event_log")
        payload = main_module.__dict__.get("_sse_payload")
        if log is None or payload is None:
            self.send_error(500, "SSE module not ready")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # 死客户端防护：socket 超时（ThreadingHTTPServer 每请求独立 socket，
        # 互不影响）。120s 无成功写出即断开该客户端。
        try:
            self.connection.settimeout(120)
        except Exception:
            pass

        def _w(s):
            self.wfile.write(s.encode("utf-8"))
            self.wfile.flush()

        # 阶段 1：全量回放当前 log（与原实现一致），记住见过的最大 _id
        last_id = -1
        for e in log.snapshot() if hasattr(log, "snapshot") else list(log):
            _w("data: " + payload(e) + "\n\n")
            try:
                eid = int(e.get("_id", -1))
                if eid > last_id:
                    last_id = eid
            except Exception:
                pass
        if hasattr(log, "max_id"):
            # log 为空时 snapshot 循环没跑，直接对齐全局最大 id
            if last_id < log.max_id() and log.max_id() >= 0 and not log.snapshot():
                last_id = log.max_id()

        # 阶段 2：轮询增量；按 _id 比较，裁剪/替换 log 都不会丢事件
        while True:
            time.sleep(0.5)
            try:
                new = log.tail(last_id) if hasattr(log, "tail") else []
            except Exception:
                new = []
            if new:
                for e in new:
                    _w("data: " + payload(e) + "\n\n")
                    try:
                        eid = int(e.get("_id", -1))
                        if eid > last_id:
                            last_id = eid
                    except Exception:
                        pass
            else:
                _w(": keepalive\n\n")

    return _safe_serve_sse


def _do_patch_once(main_module, app_dir):
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return True
        old_log = main_module.__dict__.get("_gui_event_log")
        handler = main_module.__dict__.get("GUIHandler")
        if old_log is None or handler is None:
            return False

        # 1) 换成代理日志（保留现有条目，正常启动时为空）
        proxy = EventLogProxy()
        try:
            for e in list(old_log):
                proxy.append(e)
        except Exception:
            pass
        proxy._xrz_is_proxy = True
        main_module.__dict__["_gui_event_log"] = proxy

        # 2) 绑定安全 SSE 服务（普通函数挂到类上 → 自动成实例方法）
        handler._serve_sse = _make_safe_serve_sse(main_module, app_dir)

        _INSTALLED = True
        try:
            marker = os.path.join(app_dir, "_sse_resilience_installed.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("installed_at=%s\nevent_id_seq=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), proxy._seq))
            print("[XRZ-SSE] 已安装 SSE 加固补丁（事件 id 化 + 续传 + 死客户端超时）", flush=True)
        except Exception as ex:
            print("[XRZ-SSE] 补丁已生效但写标记失败:", ex, flush=True)
        return True


def install(main_module, app_dir, timeout_s=180):
    """启动安装线程：等 pyc 模块体执行完（GUIHandler/_gui_event_log 出现）后热替换。"""

    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if _do_patch_once(main_module, app_dir):
                    return
            except Exception as ex:
                print("[XRZ-SSE] 安装异常（重试中）:", ex, flush=True)
            time.sleep(0.2)
        print("[XRZ-SSE] 安装超时（180s 内未出现目标符号），补丁未生效", flush=True)

    t = threading.Thread(target=_worker, daemon=True, name="xrz-sse-resilience")
    t.start()
    return t
