"""
终端.py — 仙人掌 Agent 主程序

唯一入口。启动后：
1. 通过 Playwright 打开 GUI 独立窗口（不污染系统浏览器）
2. 后台启动 Agent（浏览器+DeepSeek+Commander+多平台+Ollama）
3. GUI 页面通过 HTTP API 与 Agent 通信
"""
import asyncio
import sys
import os
import json
import threading
import time as _time_module
import socket
import re
import uuid
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

# 确保工作目录
WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))
os.chdir(str(WORK_DIR))

# 动态平台注册表：从 platforms.json / platforms.user.json 读取，无需改代码即可扩展
from agent_core.platform_browser import list_platforms as _list_platforms

# 常见平台中文/英文别名 → 平台 key（始终可用，叠加配置里的平台名）
_PLATFORM_ALIASES = {
    "deepseek": "deepseek", "ds": "deepseek", "仙人掌": "deepseek",
    "tongyi": "tongyi", "通义": "tongyi", "通义千问": "tongyi", "qwen": "tongyi",
    "doubao": "doubao", "豆包": "doubao",
    "yuanbao": "yuanbao", "元宝": "yuanbao",
    "chatgpt": "chatgpt", "gemini": "gemini", "kimi": "kimi", "claude": "claude",
    "wenxin": "wenxin", "文心一言": "wenxin", "zhipu": "zhipu", "智谱": "zhipu",
    "智谱清言": "zhipu", "grok": "grok", "perplexity": "perplexity",
}


def _build_platform_map() -> dict:
    """平台 key / 别名 → 平台 key 的映射（动态）。"""
    m = dict(_PLATFORM_ALIASES)
    for p in _list_platforms():
        m.setdefault(p["key"], p["key"])
        m.setdefault(p["name"], p["key"])
    return m


def _platform_names() -> dict:
    """平台 key → 展示名。"""
    return {p["key"]: p["name"] for p in _list_platforms()}

# 文本缓冲区（文件备份的持久剪贴板 + 自动清理）
from buffer_store import get_buffer_store, BufferError, DISPLAY_LIMIT

# ─── 全局状态 ───
browser_mgr = None
session = None
commander = None
agent_ready = False
active_platform = "deepseek"  # 当前活跃平台

# 平台会话缓存：commander._session 会在切换时指向这里的对应会话
_deepseek_session = None            # 主 DeepSeek 会话（launch_agent 中赋值）
_platform_sessions = {}             # platform_key -> PlatformSession（懒加载）

# 存储实时事件，供 GUI 推送
_gui_event_log = []  # list of dicts
_gui_event_lock = asyncio.Lock()
_httpd_server = None
_httpd_thread = None
_async_loop = None


def _sanitize_for_json(obj):
    """把不可 JSON 序列化的对象递归转换为字符串，避免 SSE/HTTP 序列化崩溃。
    否则 Commander 事件若携带非序列化对象（集合/字节/异常等）会让 json.dumps 抛错，
    导致 SSE 流崩溃或 HTTP 返回非 JSON 正文，前端就会出现『json 报错』。"""
    try:
        json.dumps(obj, ensure_ascii=False)
        return obj
    except (TypeError, ValueError):
        pass
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", "replace")
        except Exception:
            return str(obj)
    return str(obj)


def _sse_payload(entry: dict) -> str:
    """把事件序列化为单行 JSON（SSE 的 data 行不能含原始换行）。
    双重兜底：第一层直接序列化；失败则把 data 强制转字符串，确保永远不会抛错、
    永远不会向前端吐出非法 JSON（否则前端 JSON.parse 会报『json 错误』）。"""
    try:
        return json.dumps(
            {"type": entry.get("type"), "data": entry.get("data"), "ts": entry.get("ts")},
            ensure_ascii=False,
        )
    except Exception:
        try:
            return json.dumps(
                {"type": entry.get("type"), "data": str(entry.get("data")), "ts": entry.get("ts")},
                ensure_ascii=False,
            )
        except Exception:
            return '{"type":"error","data":"<序列化失败>"}'


def _gui_emit_nowait(event_type, data=None):
    """不等待 event_loop 直接追加事件（data 会先净化，保证可序列化）"""
    entry = {"type": event_type, "data": _sanitize_for_json(data), "ts": _time_module.time()}
    _gui_event_log.append(entry)
    if len(_gui_event_log) > 200:
        del _gui_event_log[:100]


async def _gui_emit(event_type, data=None):
    """向 GUI 前端推送实时事件"""
    _gui_emit_nowait(event_type, data)


# GUI 命令队列
_cmd_queue = asyncio.Queue()
_cmd_reply_futures = {}  # id -> asyncio.Future

# 活跃任务跟踪（用于异步命令处理）
_active_tasks = {}  # task_id -> {"status": str, "result": dict, "events": list}
_active_tasks_lock = asyncio.Lock()


def c(text, *styles):
    RESET = "\033[0m"
    return "".join(styles) + text + RESET

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"


async def launch_agent():
    """启动 Agent（浏览器 + DeepSeek + Commander）"""
    global browser_mgr, session, commander, agent_ready, _deepseek_session

    from agent_core.browser import BrowserManager
    from agent_core.session import DeepSeekSession
    from agent_core.commander import Commander
    from agent_core.memory_manager import MemoryManager

    print("[1/5] 启动浏览器...")
    # 无头开关：测试/CI 用 XRZ_HEADLESS=1 让平台浏览器不弹可见窗口
    _headless = os.environ.get("XRZ_HEADLESS") == "1"
    browser_mgr = BrowserManager(headless=_headless)
    await browser_mgr.launch()
    print("[OK] 浏览器已启动")

    print("[2/5] 检查 DeepSeek 登录...")
    await browser_mgr.navigate()
    logged_in = await browser_mgr.check_login()
    if not logged_in:
        print("[!] 请在浏览器中登录 DeepSeek...")
        await browser_mgr.wait_login(timeout=180)
        await browser_mgr.save_cookies()
    print("[OK] 登录就绪")

    print("[3/5] 初始化会话...")
    from agent_core.xrz_paths import GUI_SESSION_DIR as task_dir, BUFFERS_DIR
    task_dir.mkdir(parents=True, exist_ok=True)

    # 缓冲区自动清理（内存/磁盘管理）：启动时清一遍过期文件与垃圾文件
    try:
        get_buffer_store(BUFFERS_DIR).cleanup()
    except Exception as ex:
        print(f"[!] 缓冲区清理跳过: {ex}")

    session = DeepSeekSession(browser_mgr)
    await session.initialize()
    # 关键：启动强制开【新对话】，避免 DeepSeek 浏览器自身恢复 / 显示上一轮残留的旧任务
    # （例如之前某轮结尾的「生成MCP报告」）。否则窗口一打开就显示那段旧指令，会被误认为
    # 「脚本替用户预输了一条指令」。
    try:
        await browser_mgr.new_session()
    except Exception as e:
        print(f"[!] 新建对话跳过: {e}")
    # 注意：启动【不再】自动恢复历史（restore_conversation）。原因：自动加载会把上一轮的
    # 对话（含用户可能未重新发起的旧任务）注入会话，deepseek 窗口会显示那段旧指令，容易被
    # 误认为「脚本预设了指令」。历史恢复方案（URL 追溯 + 消息 JSON）仍已实现，需要时用户在
    # GUI 输入「恢复对话」即可手动恢复。
    # 保存主 DeepSeek 会话，供平台切换时切回
    _deepseek_session = session

    def _on_event_handler(e):
        """Commander 事件回调 → 转发到 GUI（同步追加，避免死锁）"""
        try:
            _gui_emit_nowait(e.event_type.value, e.data)
        except Exception as ex:
            print(f"[GUI emit error] {ex}")

    commander = Commander(
        browser_manager=browser_mgr,
        session=session,
        work_dir=str(task_dir),
        on_event=_on_event_handler,
    )
    await commander.start(session=session)
    commander._memory = MemoryManager(str(task_dir))
    # 把 DeepSeek 的「思考过程」也推给 GUI
    session.set_on_event(lambda etype, data: _gui_emit_nowait(etype, data))
    print("[OK] Commander 就绪")

    print("[4/5] 初始化多平台...")
    try:
        from agent_core.platform_browser import MultiPlatformManager
        from agent_core.multi_browser import set_multi_browser_manager
        multi = MultiPlatformManager()
        # 添加平台浏览器
        multi.add("deepseek")
        multi.add("tongyi")
        multi.add("doubao")
        multi.add("yuanbao")
        set_multi_browser_manager(multi)
        print("[OK] 多平台配置完成")
    except Exception as e:
        print(f"[!] 多平台跳过: {e}")

    agent_ready = True


async def _handle_command_async(cmd: str, attachments=None):
    """异步处理命令，立即返回任务ID，通过SSE推送中间事件"""
    global commander, session, active_platform, _active_tasks
    
    task_id = f"task_{int(_time_module.time()*1000)}"
    async with _active_tasks_lock:
        _active_tasks[task_id] = {"status": "running", "result": None, "events": []}
    
    # 内置命令 - 立即返回结果
    cmd_lower = cmd.strip().lower()
    result = None

    # 当前会话：优先用 commander 当前后端会话（切到平台后会指向平台会话），
    # 否则回退到主 DeepSeek 会话。恢复/列举历史都作用在「当前会话」上。
    cur_session = None
    if commander and getattr(commander, "_session", None):
        cur_session = commander._session
    elif session:
        cur_session = session

    if cmd_lower in ("quit", "exit", "q"):
        result = {"type": "system", "text": "终端已退出", "action": "close"}
    elif cmd_lower == "clear":
        result = {"type": "system", "text": "clear", "action": "clear"}
    elif cmd_lower == "status":
        result = {
            "type": "system",
            "text": f"Agent: {'就绪' if agent_ready else '启动中'} | "
                    f"浏览器: {'已连接' if browser_mgr else '未连接'} | "
                    f"平台: {active_platform} | Commander: {'就绪' if commander else '等待中'}"
        }
    elif cmd_lower in ("deep", "think"):
        if session:
            session.toggle_thinking()
            mode = "深度思考" if session.thinking_mode else "快速模式"
            result = {"type": "system", "text": f"已切换为: {mode}"}
        else:
            result = {"type": "error", "text": "会话未就绪"}
    elif cmd_lower.startswith("switch to platform:") or cmd_lower.startswith("切换到平台:"):
        rest = cmd_lower.replace("switch to platform:", "").replace("切换到平台:", "").strip()
        candidate = rest.split()[0] if rest.split() else ""
        platform_map = _build_platform_map()
        pk = platform_map.get(candidate)
        if pk:
            # 复用统一切换逻辑（懒加载浏览器 + 切换 commander 会话）
            result = await _switch_platform_async(pk)
        else:
            result = {"type": "error", "text": f"未知平台: {candidate}。可选: deepseek, tongyi, doubao, yuanbao"}
    elif cmd_lower == "help":
        result = {"type": "system", "text": """可用命令:
  help - 显示帮助
  status - 查看状态
  clear - 清屏
  deep/think - 切换深度思考
  switch to platform: tongyi - 切换到通义
  switch to platform: doubao - 切换到豆包
  switch to platform: yuanbao - 切换到元宝
  buffer write <名> <文本>  - 写入缓冲区（覆盖）
  buffer append <名> <文本> - 追加入缓冲区（分批输入用）
  buffer get <名>           - 取出缓冲区内容并输出
  buffer save <名> <文件>   - 把缓冲区内容写到另一个文件
  buffer load <名> <文件>   - 把一个文件读入缓冲区
  buffer list / clear / cleanup - 列表 / 清空 / 自动清理
  quit/exit - 退出
  其他 - 发送给 AI 执行"""}

    # 文本缓冲区命令（内置，不需要 Commander/浏览器即可用）
    elif cmd_lower.startswith("buffer"):
        result = _handle_buffer_command(cmd)

    # 列举全部独立历史任务（每个任务 = 一次独立对话）
    elif cmd_lower in ("历史任务", "tasks", "任务列表", "history"):
        try:
            if cur_session is None:
                result = {"type": "error", "text": "Agent 尚未就绪"}
            else:
                tasks = cur_session.list_tasks()
                if not tasks:
                    result = {"type": "system", "text": "暂无历史任务"}
                else:
                    lines = ["【历史任务（每个都是一次独立对话）】"]
                    for i, t in enumerate(tasks[:30], 1):
                        lines.append(
                            f"{i}. [{t.get('platform','?')}] {t.get('title','')[:60]} "
                            f"| id={t.get('id','')} | {t.get('updated_at','')[:19]}"
                        )
                    lines.append("恢复某个任务：恢复对话 <id>  或  恢复对话（最新一个）")
                    result = {"type": "system", "text": "\n".join(lines)}
        except Exception as e:
            result = {"type": "error", "text": f"列举任务失败: {e}"}

    # 手动恢复历史对话（用户显式触发；启动时不自动恢复，避免加载上一轮残留的旧任务）
    # 支持：恢复对话 / 恢复对话 <task_id>
    elif cmd_lower.startswith(("恢复对话", "恢复历史", "恢复上次会话", "restore")):
        try:
            if cur_session is None:
                result = {"type": "error", "text": "Agent 尚未就绪"}
            else:
                # 解析可选的 task_id 参数
                _arg = cmd.strip()
                _arg_lower = _arg.lower()
                _task_id = None
                for _p in ("恢复对话 ", "恢复历史 ", "恢复上次会话 ", "restore "):
                    if _arg_lower.startswith(_p):
                        _task_id = _arg[len(_p):].strip()
                        break
                restored = await cur_session.restore_conversation(task_id=_task_id or None)
                result = {"type": "system",
                          "text": f"历史恢复：{'已恢复（指定/最新任务）' if restored else '无历史可恢复'}"}
        except Exception as e:
            result = {"type": "error", "text": f"恢复失败: {e}"}

    # 新建一个独立的对话（清空本地上下文 + 浏览器开新聊天）
    elif cmd_lower in ("新对话", "新建对话", "new chat", "new conversation", "new"):
        try:
            if cur_session is None:
                result = {"type": "error", "text": "Agent 尚未就绪"}
            else:
                if getattr(cur_session, "start_new_conversation", None):
                    await cur_session.start_new_conversation()
                result = {"type": "system",
                          "text": "已开启一个全新的独立对话（此前的对话已作为独立任务保留在历史中）"}
        except Exception as e:
            result = {"type": "error", "text": f"新建对话失败: {e}"}

    if result:
        # 内置命令直接返回结果，并通过 SSE 推送给前端
        async with _active_tasks_lock:
            if task_id in _active_tasks:
                _active_tasks[task_id]["status"] = "done"
                _active_tasks[task_id]["result"] = result
        _gui_emit_nowait("ai_final_reply", result)
        return result
    
    # 普通任务 -> 交给 Commander（非阻塞）
    if not commander:
        return {"type": "error", "text": "Agent 尚未就绪，请等待启动完成"}

    # 把 GUI 选中的本地文件挂到 commander 待发送列表（仅普通任务带附件）
    if attachments:
        added = []
        for p in (attachments if isinstance(attachments, list) else [attachments]):
            try:
                pp = Path(p)
                if pp.exists():
                    commander._pending_attachments.append(str(pp.resolve()))
                    added.append(pp.name)
            except Exception:
                pass
        if added:
            _gui_emit_nowait("command_success",
                             {"text": f"已附加 {len(added)} 个文件: {', '.join(added)}（将随本条消息发到平台）"})

    # 启动后台循环
    try:
        reply = await commander.run_with_loop(user_instruction=cmd)
        final_result = {"type": "ai", "text": reply[:3000] if reply else "[无回复]"}
        async with _active_tasks_lock:
            if task_id in _active_tasks:
                _active_tasks[task_id]["status"] = "done"
                _active_tasks[task_id]["result"] = final_result
        # 关键：通过 SSE 推送最终回复，确保前端一定能看到（即使 POST 早已返回）
        _gui_emit_nowait("ai_final_reply", final_result)
        return final_result
    except Exception as e:
        import traceback
        error_result = {"type": "error", "text": str(e) + "\n" + traceback.format_exc()[:1000]}
        async with _active_tasks_lock:
            if task_id in _active_tasks:
                _active_tasks[task_id]["status"] = "done"
                _active_tasks[task_id]["result"] = error_result
        # 通过 SSE 推送错误，确保前端可见
        _gui_emit_nowait("ai_final_reply", error_result)
        return error_result


def _handle_buffer_command(cmd: str) -> dict:
    """处理 buffer 系列命令，返回与内置命令同构的结果 dict。

    不依赖 Commander / 浏览器，纯本地文件操作，因此即使 Agent 尚未就绪也能用。
    """
    try:
        store = get_buffer_store()
    except Exception as e:
        return {"type": "error", "text": f"缓冲区初始化失败: {e}"}

    parts = cmd.split(None, 2)  # ["buffer", 子命令, 剩余参数]
    if len(parts) < 2:
        return {"type": "system",
                "text": "用法: buffer <write|append|get|list|save|load|clear|cleanup> ..."}
    sub = parts[1].lower()
    rest = parts[2] if len(parts) > 2 else ""

    try:
        if sub in ("write", "append"):
            sp = rest.split(None, 1)
            if len(sp) < 1:
                return {"type": "system", "text": f"用法: buffer {sub} <名称> [内容]"}
            name = sp[0]
            text = sp[1] if len(sp) > 1 else ""
            info = store.write(name, text, append=(sub == "append"))
            return {"type": "system", "text": info["msg"]}

        elif sub == "get":
            sp = rest.split(None, 1)
            name = sp[0] if sp else ""
            if not name:
                return {"type": "system", "text": "用法: buffer get <名称>"}
            info = store.get(name)
            if not info["found"]:
                return {"type": "system", "text": info["msg"]}
            content = info["content"]
            # 超长内容自动落盘，避免一次性塞爆对话 / JSON
            if len(content) > DISPLAY_LIMIT:
                out = store.root / f"{name}.export.txt"
                out.write_text(content, encoding="utf-8")
                preview = content[:500]
                return {"type": "ai",
                        "text": f"[缓冲区 {name} 共 {info['size']} 字节，已自动导出到 {out}]\n\n"
                                f"预览:\n{preview}\n..."}
            return {"type": "ai", "text": content}

        elif sub == "list":
            info = store.list()
            return {"type": "system", "text": info["msg"]}

        elif sub == "save":
            sp = rest.split(None, 1)
            if len(sp) < 2:
                return {"type": "system", "text": "用法: buffer save <名称> <输出文件>"}
            name, outp = sp
            info = store.save(name, outp)
            return {"type": "system", "text": info["msg"]}

        elif sub == "load":
            sp = rest.split(None, 1)
            if len(sp) < 2:
                return {"type": "system", "text": "用法: buffer load <名称> <源文件>"}
            name, inp = sp
            info = store.load(name, inp)
            return {"type": "system", "text": info["msg"]}

        elif sub == "clear":
            name = rest.strip() or None
            info = store.clear(name)
            # 清空全部后顺手做一次自动清理
            if name is None:
                try:
                    store.cleanup()
                except Exception:
                    pass
            return {"type": "system", "text": info["msg"]}

        elif sub == "cleanup":
            info = store.cleanup()
            return {"type": "system", "text": info["msg"]}

        else:
            return {"type": "system",
                    "text": f"未知 buffer 子命令: {sub}\n"
                            f"用法: buffer <write|append|get|list|save|load|clear|cleanup>"}
    except BufferError as e:
        return {"type": "error", "text": str(e)}
    except Exception as e:
        return {"type": "error", "text": f"缓冲区命令出错: {e}"}


def _post_command_to_loop(cmd: str, attachments=None) -> dict:
    """从 HTTP 线程安全地将命令投递到事件循环（立即返回 accepted，结果通过 SSE 推送）"""
    loop = _async_loop
    if loop is None or not loop.is_running():
        return {"type": "error", "text": "事件循环未运行"}

    # 立即提交到事件循环，不等结果（避免阻塞 HTTP 线程）
    # 最终结果会通过 _handle_command_async 内 emit 的 ai_final_reply 事件经 SSE 推送给前端
    asyncio.run_coroutine_threadsafe(_handle_command_async(cmd, attachments), loop)
    return {"type": "accepted", "text": "命令已接收，正在处理...", "async_task": True}


async def _switch_platform_async(platform_key: str) -> dict:
    """
    实际执行平台切换（在事件循环中运行，同步返回最终结果）。

    关键：不仅导航浏览器，还要把 commander._session 切换到目标平台，
    这样后续任务才真正跑在新平台上（而不是永远用 DeepSeek）。
    """
    global active_platform, commander
    platform_names = _platform_names()
    name = platform_names.get(platform_key, platform_key)

    # ── 切回 DeepSeek：用主浏览器 + 主会话 ──
    if platform_key == "deepseek":
        active_platform = "deepseek"
        if commander and _deepseek_session is not None:
            commander._session = _deepseek_session
            try:
                commander._session.set_system_prompt(commander._system_prompt)
            except Exception:
                pass

        # 关键：DeepSeek 浏览器必须在用户视野里出现。原来的代码在以下三种情况下会
        # 静默「打不开」：(a) launch_agent 启动时 Chromium 就崩了，browser_mgr 存在但
        # _browser 为 None；(b) 浏览器之后被关掉；(c) navigate() 抛异常被吞。下面
        # 三步分别兜底 + 上抛错误，不再让用户看到「已切换」但窗口不出现。
        if browser_mgr is None:
            _gui_emit_nowait("command_error",
                             {"text": f"{name} 浏览器管理器未初始化（启动时 Playwright 启动失败）。请运行：python -m playwright install chromium"})
            return {"type": "error", "text": f"{name} 浏览器管理器未初始化"}

        try:
            # (a)(b) 浏览器没活着 → 自愈重启（launch 内部已带锁文件清理 + 重试）
            _br = getattr(browser_mgr, "_browser", None)
            _dead = _br is None or (
                hasattr(_br, "is_closed") and callable(_br.is_closed) and _br.is_closed()
            )
            if _dead:
                _gui_emit_nowait("command_executing",
                                 {"text": f"{name} 浏览器未运行，正在启动..."})
                await browser_mgr.launch()
            # (c) 导航不再吞异常：失败要让用户知道（不然就是「点了没反应」）
            await browser_mgr.navigate()
        except Exception as ex:
            import traceback as _tb
            err_text = f"切换 {name} 失败: {type(ex).__name__}: {ex}"
            logger.exception("切换 DeepSeek 失败")
            _gui_emit_nowait("command_error", {"text": err_text})
            return {"type": "error", "text": err_text + "\n" + _tb.format_exc()[:400]}

        _gui_emit_nowait("command_success", {"text": f"已切换到 {name}"})
        return {"type": "system", "text": f"已切换到 {name}"}

    # ── 切到第三方平台：懒加载启动浏览器 + 换会话 ──
    from agent_core.multi_browser import get_multi_browser_manager
    from agent_core.platform_browser import PlatformSession
    mgr = get_multi_browser_manager()
    if mgr is None:
        return {"type": "error", "text": "多平台管理器未初始化"}

    plat_browser = mgr.get(platform_key)
    if plat_browser is None:
        mgr.add(platform_key)
        plat_browser = mgr.get(platform_key)
    if plat_browser is None:
        return {"type": "error", "text": f"无法创建平台: {platform_key}"}

    # 懒加载：首次切换时才真正启动该平台的 Chromium
    # 自愈：若启动/导航时浏览器被意外关闭（TargetClosed / browser has been closed，
    # 常见于同进程多开有头 Chromium 第 3 个窗口 GPU 崩溃、或上次残留进程占目录），
    # 清理半死浏览器后重试一次，而不是直接失败。
    import traceback as _tb
    launch_err = None
    for _attempt in range(2):
        try:
            if plat_browser._browser is None:
                _gui_emit_nowait("command_executing",
                                 {"text": f"正在启动 {name} 浏览器..." if _attempt == 0
                                  else f"重试启动 {name} 浏览器..."})
                await plat_browser.launch()
            await plat_browser.navigate_to_chat()
            launch_err = None
            break  # 启动+导航成功
        except Exception as ex:
            launch_err = ex
            err_text = str(ex)
            # 浏览器被关类错误才重试；其它错误（如 URL 错误）直接上报
            if "has been closed" not in err_text and "TargetClosed" not in err_text \
                    and "Target page" not in err_text:
                return {"type": "error", "text": f"启动 {name} 失败: {ex}\n{_tb.format_exc()[:500]}"}
            # 清理半死浏览器，准备重试
            try:
                await plat_browser.close()
            except Exception:
                pass
            plat_browser._browser = None
            plat_browser._page = None
            if _attempt == 1:
                return {"type": "error", "text": f"启动 {name} 失败（重试后仍被杀）: {ex}\n{_tb.format_exc()[:500]}"}
    if launch_err is not None:
        return {"type": "error", "text": f"启动 {name} 失败: {launch_err}\n{_tb.format_exc()[:500]}"}

    # 检查登录态（未登录则提示用户在弹窗中登录）
    logged_in = False
    try:
        logged_in = await plat_browser.check_login()
    except Exception:
        pass

    # 创建/复用该平台会话，并切换 commander 的后端
    if platform_key not in _platform_sessions:
        ps = PlatformSession(plat_browser)
        ps.set_on_event(_gui_emit_nowait)  # 把「思考过程」等事件推给 GUI 的 SSE
        _platform_sessions[platform_key] = ps
        # 首次创建即恢复该平台历史（URL 优先，失败回退 JSON）
        try:
            restored = await ps.restore_conversation()
            print(f"[{'OK' if restored else '··'}] {name} 历史恢复: {'已恢复' if restored else '无历史'}")
        except Exception as e:
            print(f"[!] {name} 历史恢复跳过: {e}")
    if commander:
        commander._session = _platform_sessions[platform_key]
        try:
            commander._session.set_system_prompt(commander._system_prompt)
        except Exception:
            pass

    active_platform = platform_key
    login_note = "" if logged_in else "（尚未登录，请在弹出的窗口中登录后再发任务）"
    result = {"type": "system", "text": f"已切换到 {name}{login_note}"}
    _gui_emit_nowait("command_success", result)
    return result


# ─── 文件上传 / 全局搜索辅助 ───

def _parse_multipart(body: bytes, content_type: str):
    """最小 multipart/form-data 解析，返回 [(field_name, filename, data_bytes), ...]。
    仅处理 FormData 上传（单/多文件）。足够前端 fetch FormData 使用。"""
    parts = []
    m = re.search(r'boundary=([^;]+)', content_type or "")
    if not m:
        return parts
    boundary = m.group(1).strip().strip('"').encode("utf-8")
    delimiter = b"--" + boundary
    segments = body.split(delimiter)
    for seg in segments:
        if seg in (b"", b"--", b"\r\n", b"--\r\n"):
            continue
        if b"\r\n\r\n" not in seg:
            continue
        head, _, content = seg.partition(b"\r\n\r\n")
        if content.endswith(b"\r\n"):
            content = content[:-2]
        head_str = head.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]*)"', head_str)
        fn_m = re.search(r'filename="([^"]*)"', head_str)
        field_name = name_m.group(1) if name_m else None
        filename = fn_m.group(1) if fn_m else None
        parts.append((field_name, filename, content))
    return parts


def _search_roots():
    """全局搜索的根目录（常见用户目录 + 项目目录 + 数据根）。"""
    roots = []
    up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for sub in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Desktop"):
        p = os.path.join(up, sub)
        if os.path.isdir(p):
            roots.append(p)
    roots.append(str(WORK_DIR))
    try:
        from agent_core import xrz_paths
        roots.append(str(xrz_paths.DATA_ROOT))
    except Exception:
        pass
    # 去重保序
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _search_files(query, ext_filter="", max_results=60, max_depth=5):
    """在常见用户目录递归搜文件名包含 query 的文件（不区分大小写）。

    限制深度与总数，避免全盘遍历卡死；收集够数量立即返回。
    """
    query = (query or "").strip().lower()
    exts = None
    if ext_filter:
        exts = set(e.lower() for e in ext_filter.split(",") if e.strip())
    results = []
    for root in _search_roots():
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath[len(root):].count(os.sep)
                if depth > max_depth:
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    if query and query not in fn.lower():
                        continue
                    ext = os.path.splitext(fn)[1].lower()
                    if exts and ext not in exts:
                        continue
                    full = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(full)
                    except Exception:
                        continue
                    results.append({
                        "path": full,
                        "name": fn,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
                    if len(results) >= max_results:
                        return results
        except Exception:
            continue
        if len(results) >= max_results:
            break
    results.sort(key=lambda r: r.get("mtime", 0), reverse=True)
    return results[:max_results]


class GUIHandler(BaseHTTPRequestHandler):
    """GUI HTTP 请求处理器"""
    gui_html_path = WORK_DIR / "gui.html"

    def do_GET(self):
        if self.path in ("/", "/gui.html", "/index.html"):
            if self.gui_html_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(self.gui_html_path.read_bytes())
            else:
                self.send_error(404, "gui.html not found")
        elif self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "agent_ready": agent_ready,
                "browser": "connected" if browser_mgr else "disconnected",
                "commander": "ready" if commander else "not ready",
                "platform": active_platform,
                "port": 8888,
            })
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/conversations":
            # 返回全部独立历史任务（跨平台），供 GUI 历史对话面板展示
            try:
                from agent_core.session import list_all_tasks
                tasks = list_all_tasks()
            except Exception:
                tasks = []
            self._send_json(200, {"tasks": tasks})
        elif self.path == "/platforms":
            # 返回全部已注册平台（动态，来自 platforms.json / platforms.user.json），
            # 供 GUI 动态渲染平台切换按钮 + 命令行自动补全。
            try:
                plats = _list_platforms()
            except Exception:
                plats = []
            self._send_json(200, {"platforms": plats})
        elif self.path.startswith("/search"):
            try:
                qs = parse_qs(urlparse(self.path).query)
                q = qs.get("q", [""])[0]
                ext = qs.get("ext", [""])[0]
                results = _search_files(q, ext_filter=ext)
            except Exception as e:
                results = []
                print(f"[search err] {e}")
            self._send_json(200, {"results": results})
        else:
            self.send_error(404)

    def _serve_sse(self):
        """Server-Sent Events: 推送实时事件流（同步模式）"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # 发送已有事件快照
        for e in _gui_event_log:
            data_str = _sse_payload(e)
            try:
                self.wfile.write(f"data: {data_str}\n\n".encode("utf-8"))
                self.wfile.flush()
            except:
                return
        # 持续推送新事件
        last_idx = len(_gui_event_log)
        while True:
            _time_module.sleep(0.5)
            new_entries = _gui_event_log[last_idx:]
            for e in new_entries:
                data_str = _sse_payload(e)
                try:
                    self.wfile.write(f"data: {data_str}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except:
                    return
            if new_entries:
                last_idx = len(_gui_event_log)
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            except:
                break

    def do_POST(self):
        if self.path == "/command":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
                cmd = body.get("command", "").strip()
                attachments = body.get("attachments") or []
                if not isinstance(attachments, list):
                    attachments = [attachments]
            except Exception:
                cmd = ""

            if not cmd:
                self._send_json(400, {"type": "error", "text": "空命令"})
                return

            result = _post_command_to_loop(cmd, attachments)
            self._send_json(200, result)
        elif self.path == "/platform":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
                platform_key = body.get("platform", "").strip()
            except Exception:
                platform_key = ""

            platform_names = _platform_names()
            if platform_key not in platform_names:
                self._send_json(400, {"type": "error", "text": f"无效平台: {platform_key}"})
                return

            # 直接执行切换（在事件循环中），同步返回结果，避免双重渲染
            loop = _async_loop
            if loop is None or not loop.is_running():
                self._send_json(200, {"type": "error", "text": "事件循环未运行"})
                return
            future = asyncio.run_coroutine_threadsafe(_switch_platform_async(platform_key), loop)
            try:
                # 首次切换需启动 Chromium + 加载页面，给足 120s
                result = future.result(timeout=120)
            except Exception as e:
                result = {"type": "error", "text": f"切换失败: {e}"}
            self._send_json(200, result)
        elif self.path == "/upload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length > 0 else b""
                ctype = self.headers.get("Content-Type", "")
                parts = _parse_multipart(body_bytes, ctype)
                from agent_core import xrz_paths
                attach_dir = xrz_paths.DATA_ROOT / "gui_attachments"
                attach_dir.mkdir(parents=True, exist_ok=True)
                saved = []
                for _fn, filename, data in parts:
                    if filename is None or not data:
                        continue
                    orig = os.path.basename(filename)
                    ext = os.path.splitext(orig)[1]
                    safe = uuid.uuid4().hex + ext
                    dest = attach_dir / safe
                    dest.write_bytes(data)
                    saved.append({"path": str(dest), "name": orig, "size": len(data)})
                if not saved:
                    self._send_json(400, {"type": "error", "text": "未收到文件"})
                else:
                    self._send_json(200, {"type": "ok", "files": saved})
            except Exception as e:
                self._send_json(500, {"type": "error", "text": f"上传失败: {e}"})
        else:
            self.send_error(404)

    def _send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        except Exception:
            # 兜底：任何情况下都返回合法 JSON，绝不返回 HTML 错误页
            # （否则前端 resp.json() 会抛『json 报错』）
            body = json.dumps(
                {"type": "error", "text": f"响应序列化失败: {data!r}"}, ensure_ascii=False
            ).encode("utf-8")
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 隐藏 HTTP 日志


def start_gui_server():
    """启动 GUI HTTP 服务器（阻塞，在线程中运行）"""
    global _httpd_server

    class ReuseHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    _httpd_server = ReuseHTTPServer(("127.0.0.1", 8888), GUIHandler)
    print("\n  [HTTP] GUI 服务器已启动: http://127.0.0.1:8888")
    try:
        _httpd_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _httpd_server.server_close()
        print("  [HTTP] GUI 服务器已关闭")


def cmd_loop():
    """命令行输入循环（后台线程）"""
    print("\n  输入 help 查看命令，输入 quit 退出\n")
    while True:
        try:
            text = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "q"):
            _shutdown_event.set()
            break
        try:
            result = _post_command_to_loop(text)
        except Exception as e:
            result = {"type": "error", "text": f"命令投递失败: {e}"}
        rtype = result.get("type", "?")
        rtext = result.get("text", "")
        if rtype == "system" and rtext == "clear":
            os.system("cls" if os.name == "nt" else "clear")
        else:
            prefix = {"ai": "", "system": "[系统] ", "error": "[错误] "}.get(rtype, "")
            display = rtext[:500] if rtype == "ai" else rtext
            print(f"\n{prefix}{display}\n")


def wait_for_http(port, timeout=5):
    """等待 HTTP 服务器就绪"""
    start = _time_module.time()
    while _time_module.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            _time_module.sleep(0.2)
    return False


# ─── Playwright GUI 窗口管理 ───
_gui_pw = None
_gui_browser = None
_gui_ctx = None
_gui_page = None
_gui_opened = False
# 关闭/退出事件：CLI 输入 quit、或 Ctrl-C 时置位，main 的保活循环据此退出
_shutdown_event = threading.Event()


def _open_gui_native(url: str):
    """用 Playwright 弹出【独立、可控】的窗口。

    采用独立的 chromium.launch + new_context + new_page：这是一个全新的 Chromium 进程
    + 独立 profile，与用户日常使用的默认浏览器【完全无关】，绝不会劫持默认浏览器。

    注意：Chromium 的 --app 应用模式虽无地址栏，但 Playwright 无法跟踪其 page 句柄、
    不可控，故不采用。本方案窗口独立、可控、可设标题，满足「独立软件窗口」的基本要求。

    绝不回退到 webbrowser.open()：一旦 Playwright 失败，只打印 URL 让用户手动打开，
    避免像上一版 pywebview 那样劫持用户的默认浏览器。
    """
    global _gui_opened
    if _gui_opened:
        print(f"  [GUI] 已有窗口: {url}")
        return
    _gui_opened = True

    def _bg():
        import asyncio as _asyncio
        import traceback as _tb
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        try:
            from playwright.async_api import async_playwright

            async def _inner():
                pw = await async_playwright().start()
                # 独立的 new_context + new_page：这是一个全新的 Chromium 进程 + 独立 profile，
                # 与用户日常使用的默认浏览器完全无关，绝不会劫持默认浏览器。
                # （注：Chromium --app 应用模式虽无地址栏，但 Playwright 无法跟踪其 page 句柄，
                #  故采用可控的独立窗口方案——窗口独立、可控，不与默认浏览器混用。）
                browser = await pw.chromium.launch(
                    headless=False,
                    args=[
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--window-size=1280,860",
                        "--window-position=120,80",
                    ],
                )
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="commit", timeout=20000)

                # 设置 OS 标题栏文字
                try:
                    await page.evaluate("document.title = '仙人掌 Agent 控制面板'")
                except Exception:
                    pass
                try:
                    await page.bring_to_front()
                except Exception:
                    pass
                print("  [GUI] 独立应用窗口已弹出（无浏览器外框）", flush=True)

                # 保活：用户关窗即清理并退出
                while not page.is_closed():
                    await asyncio.sleep(2)
                try:
                    await browser.close()
                except Exception:
                    pass
                try:
                    await pw.stop()
                except Exception:
                    pass
                print("  [GUI] 窗口已关闭", flush=True)

            loop.run_until_complete(_inner())
        except Exception as e:
            print(f"  [GUI] Playwright 窗口启动失败: {e}", flush=True)
            print(_tb.format_exc(), flush=True)
            print(f"  [GUI] 请手动在浏览器打开: {url}", flush=True)

    t = threading.Thread(target=_bg, daemon=True)
    t.start()


def _open_gui_in_playwright(url: str):
    """兼容别名 → 独立 Playwright 应用窗口"""
    return _open_gui_native(url)


def _run_gui_pw(url: str):
    """兼容别名"""
    return _open_gui_native(url)


def main():
    global _httpd_thread, _async_loop

    print("=" * 50)
    print("    仙人掌 Agent")
    print("    DeepSeek + 通义千问 + 豆包 + 元宝 + ChatGPT + Gemini + Ollama")
    print("=" * 50)
    print()

    # 1. 先启动 HTTP 服务器（在主线程，不阻塞）
    _httpd_thread = threading.Thread(target=start_gui_server, daemon=False)
    _httpd_thread.start()

    # 等待 HTTP 服务器就绪
    if not wait_for_http(8888, timeout=5):
        print("[ERROR] HTTP 服务器启动超时！")
        sys.exit(1)
    print("[OK] HTTP 服务器已就绪")

    # 2. 启动 Agent（主线程持有事件循环）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _async_loop = loop

    async def _main():
        # (0) 把旧版落在 C 盘用户目录的数据迁移到 D 盘 xrz_data（仅复制，不占 C 盘）
        try:
            from agent_core.xrz_paths import maybe_migrate
            maybe_migrate()
        except Exception:
            pass

        # (a) 启动 Agent 核心（浏览器 / commander）
        #     容错：浏览器启动失败（如二进制缺失/损坏）不应直接崩掉整个进程，
        #     至少让 HTTP 服务与 GUI 起来，并给出明确错误提示。
        try:
            await launch_agent()
            print("\n  ✓ Agent 启动完成")
        except Exception as e:
            print("\n  [!] Agent 启动失败（浏览器未能启动）:")
            print(f"      {type(e).__name__}: {e}")
            print("      应用仍会打开，但需先解决浏览器问题（通常是 Playwright 浏览器未安装）。")
            print("      可运行: python -m playwright install chromium")

        # (b) 启动命令行输入线程
        cli_thread = threading.Thread(target=cmd_loop, daemon=True)
        cli_thread.start()

        # (c) 弹出独立 GUI 窗口（控制面板；关窗不杀 agent）
        #     若由原生桌面壳（desktop_app.py）启动，会设 XRZ_NO_GUI=1：
        #     此时后端只做 HTTP 服务，不再弹 Playwright 浏览器窗口（界面由 Qt 原生窗口承载），
        #     避免重复弹两个窗口。
        url = "http://127.0.0.1:8888"
        if os.environ.get("XRZ_NO_GUI") == "1":
            print("  [GUI] XRZ_NO_GUI=1 → 后端仅提供 HTTP 服务，界面由原生窗口承载")
        else:
            _open_gui_native(url)

        # (d) 打印就绪信息
        print("\n" + "=" * 50)
        print("  Agent 已就绪！")
        print("  - 独立应用窗口（非默认浏览器）已弹出")
        print("  - 在本窗口输入命令或使用 GUI")
        print("  - 输入 quit 退出 / 关闭窗口仅关面板（agent 继续运行）")
        print("=" * 50)
        print()

        # (e) 保持事件循环运行：让 HTTP / CLI 线程通过 run_coroutine_threadsafe
        #     调度协程（切换平台、执行命令）。
        #     必须用 await asyncio.sleep（让出控制权），不能用 time.sleep（会卡死循环）。
        while not _shutdown_event.is_set():
            await asyncio.sleep(0.5)

    loop.run_until_complete(_main())

    # 3. 清理（_shutdown_event 已被置位：quit / Ctrl-C）
    print("\n正在关闭...")
    if browser_mgr:
        try:
            loop.run_until_complete(browser_mgr.close())
        except Exception:
            pass
    loop.close()

    # 8. 关闭 HTTP 服务器
    if _httpd_server:
        try:
            threading.Thread(target=_httpd_server.shutdown, daemon=True).start()
        except:
            pass


if __name__ == "__main__":
    main()
