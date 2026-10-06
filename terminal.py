"""
terminal.py —— 仙人掌 Agent 主程序入口（CORS 增强包装）。

原始后端逻辑在 terminal.pyc（源码已丢失，仅余编译产物）。
本文件做两件事：
  1. 给 http.server.BaseHTTPRequestHandler 打补丁，正确处理 OPTIONS 预检。
     浏览器对跨域 JSON POST（如 fetch('/command')）会先发 OPTIONS 预检；
     原后端没有 do_OPTIONS，返回 501 → 浏览器直接拦截真实 POST，
     前端报 "Failed to fetch" —— 这正是「发送失败 / 软件连不上」的根因之一。
  2. 以 __main__ 身份执行 terminal.pyc，启动原后端（其余逻辑完全不变）。
"""

import os
import sys
import types
import marshal
import socket
import http.server


# ── 1) CORS 预检补丁 ──
def _do_options(self):
    self.send_response(200)
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
    self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
    self.send_header("Access-Control-Max-Age", "86400")
    self.end_headers()


http.server.BaseHTTPRequestHandler.do_OPTIONS = _do_options


# ── 2) 非交互模式：input() 补丁 ──
# 当 XRZ_NO_GUI=1 且 stdin 非交互终端（后台/桌面 App 拉起）时，cmd_loop 里的
# input() 会阻塞 asyncio 事件循环线程 → 浏览器初始化协程得不到调度 → 卡在 [1/5]。
# 修复：把内置 input 换成哨兵，立即抛 EOFError，cmd_loop 捕获后自动退出。
# GUI 的 HTTP API 才是真正的接口，控制台输入循环在无人值守场景下无用。

import builtins

def _patch_input_noninteractive():
    if os.environ.get("XRZ_NO_GUI") != "1":
        return
    if not sys.stdin or not sys.stdin.isatty():
        def _nonblocking_input(prompt=""):
            if prompt:
                try:
                    print(prompt, end="", flush=True)
                except Exception:
                    pass
            raise EOFError("non-interactive mode (XRZ_NO_GUI=1, no TTY)")
        builtins.input = _nonblocking_input
        print("[XRZ] 非交互模式：input() 已替换为哨兵，cmd_loop 将立即退出")

_patch_input_noninteractive()


# ── 2.5) 单实例保护：端口 8888 已被占用说明已有后端在运行 ──
# 严禁再启动第二个后端，否则会打开「第二个 DeepSeek 浏览器」（同一份
# user_data_dir 被两个持久化上下文抢占，导致未登录 / 冲突）。
# 检测到已有实例时直接退出，让现有后端继续服务。
def _port_in_use(port: int = 8888, host: str = "127.0.0.1") -> bool:
    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _s.settimeout(1.0)
        return _s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        try:
            _s.close()
        except Exception:
            pass


if _port_in_use(8888):
    print("[XRZ] 端口 8888 已被占用（已有后端实例在运行）。"
          "拒绝启动第二个后端，避免同时打开两个 DeepSeek 浏览器。"
          "若需重启，请先关闭已有实例。", flush=True)
    sys.exit(0)


# ── 2.8) 事件循环自愈：任何逃逸异常都不允许打死后端 ──
# 【实测事故】生成 PPT 时清理中间文件触发环境级删除拦截，对方抛 SystemExit。
# asyncio 的 Task.__step 对 SystemExit / KeyboardInterrupt 是「set_exception 后
# 原样再 raise」，异常一路穿透 run_forever()，事件循环线程直接死掉 ——
# 此后每条指令都只能返回「事件循环未运行」，后端彻底报废、必须重启进程。
# 这里给 run_forever 套一层：拦截逃逸异常、打印堆栈、继续服务。
# （真正该修的地方在 commander / 工具链，这里是最后一道兜底。）
import asyncio
import traceback

_orig_run_forever = asyncio.BaseEventLoop.run_forever


def _resilient_run_forever(self):
    _escape = 0
    while True:
        try:
            _orig_run_forever(self)
            return  # 正常 stop()（如 /shutdown），正常退出
        except BaseException:  # noqa: BLE001
            if sys.is_finalizing():
                raise
            _escape += 1
            print("[XRZ] ⚠ 事件循环捕获到逃逸异常，已拦截，后端继续服务：", flush=True)
            traceback.print_exc()
            if self.is_closed() or _escape >= 20:
                print("[XRZ] 事件循环已关闭或反复异常，线程退出", flush=True)
                return
            continue


asyncio.BaseEventLoop.run_forever = _resilient_run_forever


# ── 3) 执行原后端（terminal.pyc 源码已丢失，仅余编译产物）──
# 用 exec 直接运行其字节码，并以 __main__ 身份触发 main()。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_pyc = os.path.join(_HERE, "terminal.pyc")
with open(_pyc, "rb") as _f:
    _f.read(16)  # 跳过 pyc 头（magic+bit_field+timestamp+size = 16 字节）
    _code = marshal.load(_f)

_mod = types.ModuleType("__main__")
_mod.__file__ = _pyc
_mod.__name__ = "__main__"
sys.modules["__main__"] = _mod

# ── 2.9) SSE 加固热补丁（exec 前挂安装线程，pyc 起来后自动热替换）──
# 修复：_gui_event_log 裁剪 200 条时游标漂移，导致老 SSE 客户端永久收不到
# ai_final_reply（面板任务卡「运行中」、发送锁死）。详见 _sse_resilience.py。
try:
    import _sse_resilience as _xrz_sse
    _xrz_sse.install(_mod, _HERE)
except Exception as _e:  # 补丁失败不影响后端正常启动
    print("[XRZ-SSE] 热补丁安装线程启动失败（不影响后端）:", _e, flush=True)

# ── 2.9.1) 文件列表热补丁：让 /files 与 /attachments 列出任务产物目录 ──
# 修复：GUI 侧边栏只看得到 gui_attachments（上传），Agent 生成的 docx/pptx/xlsx/
# txt 等产物（落在 XianRenZhang_tasks/）永远不进列表 → 产物预览对"自己生成的文件"不可达。
# 详见 _files_listing_fix.py。
try:
    import _files_listing_fix as _xrz_files
    _xrz_files.install(_mod, _HERE)
except Exception as _e:  # 补丁失败不影响后端正常启动
    print("[XRZ-Files] 热补丁安装线程启动失败（不影响后端）:", _e, flush=True)

# ── 2.9.2) 只读 /dom 端点：dump 当前活动浏览器页面 HTML，用于核对平台选择器 ──
# 网页更新后 platforms.json 的选择器会过期，靠它核对"标志还是旧的"（详见 _dom_dump_patch.py）。
try:
    import _dom_dump_patch as _xrz_dom
    _xrz_dom.install(_mod, _HERE)
except Exception as _e:  # 补丁失败不影响后端正常启动
    print("[XRZ-DOM] 热补丁安装线程启动失败（不影响后端）:", _e, flush=True)

# ── 2.9.3) 多任务真并行：每个任务一张独立标签页，不再排队 ──
# 用户明确要求：子母代理能在同一浏览器开两个标签，多任务必须并行，
# 不允许出现「上一个任务正在执行需要排队」。详见 _parallel_tasks_patch.py。
try:
    import _parallel_tasks_patch as _xrz_par
    _xrz_par.install(_mod, _HERE)
except Exception as _e:  # 补丁失败不影响后端正常启动
    print("[XRZ-Parallel] 热补丁安装线程启动失败（不影响后端）:", _e, flush=True)

# ── 2.9.4) 自动更新：给 pyc 后端补 /check_update、/version 端点 ──
# pyc 是编译产物改不了源码，用热补丁给 GUIHandler 挂新路由（见 _auto_update_patch.py）。
try:
    import _auto_update_patch as _xrz_upd
    _xrz_upd.install(_mod, _HERE)
except Exception as _e:  # 补丁失败不影响后端正常启动
    print("[XRZ-AutoUpdate] 热补丁安装线程启动失败（不影响后端）:", _e, flush=True)

exec(_code, _mod.__dict__)

