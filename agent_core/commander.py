"""
commander.py — 仙人掌 Agent 主控制器
- 总工程师：调度工具 + 子代理 + 记忆
- 支持 continue/remember/recall 指令
- 自动摘要提醒
"""
import asyncio
import time
import logging
import uuid
import re
import os
import sys
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
from types import SimpleNamespace

from .protocol import Protocol, ExecutionResult
from .session import DeepSeekSession, SessionConfig, MessageRole
# 注意：子代理实际实现在 subagent_manager.py（同浏览器子窗口 + 完整 Commander 循环），
# 由 _browser_research/_browser_visit 工具通过 get_subagent_manager 使用。旧的 subagent.py
# 仅保留 BrowserSubAgent 基类，已不再被本模块引用，故此处不导入它。
from .memory_manager import MemoryManager
# 动态平台注册表（适配更多网页 AI）：从 platforms.json / platforms.user.json 读取
from agent_core.platform_browser import list_platforms as _list_platforms

logger = logging.getLogger("commander")


class EventType(Enum):
    THINKING = "thinking"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_ERROR = "tool_error"
    COMMAND_DETECTED = "command_detected"
    COMMAND_EXECUTING = "command_executing"
    COMMAND_SUCCESS = "command_success"
    COMMAND_ERROR = "command_error"
    AI_FINAL_REPLY = "ai_final_reply"
    MEMORY_REMINDER = "memory_reminder"
    CONTINUE_READY = "continue_ready"
    CORRECTION_SENT = "correction_sent"
    ERROR = "error"
    QUESTION = "question"
    SESSION_UPDATED = "session_updated"
    INTERRUPT = "interrupt"  # 用户中断
    MESSAGE_QUEUED = "message_queued"  # 插话消息入队
    MESSAGE_PROCESSED = "message_processed"  # 插话消息处理
    # WARNING/INFO：系统提示类事件（不是错误）。之前没有这两个类型，代码只能
    # 回退成 ERROR 事件 —— GUI 会把「新指令已排队」这类正常提示也画成红色报错。
    WARNING = "warning"
    INFO = "info"


@dataclass
class AgentEvent:
    event_type: EventType
    data: any = None


# ============================================================
# 工具注册表
# ============================================================
class ToolRegistry:
    """工具注册表，支持子代理工具"""

    def __init__(self, commander):
        self._commander = commander
        self._tools: dict = {}

    def register(self, name: str, description: str, fn: Callable):
        self._tools[name] = {"description": description, "fn": fn}

    def list_tools(self) -> list:
        return [
            {"name": name, "description": info["description"]}
            for name, info in self._tools.items()
        ]

    # 常见「模型臆想的工具名」→ 真实工具 的别名映射。
    # 浏览器 AI 不一定严格照搬注册名，常凭语义编造 generate_word_report / make_ppt 等，
    # 这里做一层兜底，避免「未知工具」直接把整条任务判死。
    TOOL_ALIASES = {
        # Word / PPT
        "generate_word_report": "docx_create", "make_word": "docx_create",
        "create_word": "docx_create", "word_create": "docx_create",
        "make_docx": "docx_create", "docx": "docx_create",
        "generate_docx": "docx_create", "create_docx": "docx_create",
        "generate_ppt": "pptx_create", "make_ppt": "pptx_create",
        "ppt_create": "pptx_create", "pptx": "pptx_create",
        "create_ppt": "pptx_create",
        # PDF
        "make_pdf": "pdf_create", "create_pdf": "pdf_create", "pdf": "pdf_create",
        "generate_pdf": "pdf_create", "export_pdf": "pdf_create",
        # 文件读写
        "write_file": "file_write", "create_file": "file_write",
        "save_file": "file_write", "write_text": "file_write",
        "read_file": "file_read", "read_text": "file_read",
        "edit_file": "file_edit", "modify_file": "file_edit",
        "append_file": "file_edit", "update_file": "file_edit",
        "make_dir": "dir_create", "mkdir": "dir_create",
        "delete_file": "file_delete", "remove_file": "file_delete",
        "rm": "file_delete", "ls": "file_list", "list_dir": "file_list",
        # 搜索
        "search": "grep", "grep_search": "grep", "find": "glob",
        "search_files": "glob", "fetch": "web_fetch", "fetch_url": "web_fetch",
        "search_web": "websearch", "web_search": "websearch",
        # 任务 / 记忆
        "todo": "todowrite", "todo_list": "todoread", "read_todo": "todoread",
        "remember_info": "remember", "recall_info": "recall",
        # 浏览器
        "click": "browser_click", "fill": "browser_fill",
        "screenshot": "browser_screenshot", "search_browser": "browser_search",
        # 子代理
        "research": "browser_research", "visit": "browser_visit",
        # 收尾（模型常用 answer / respond / reply 给最终答复，统一映射到 done）
        "answer": "done", "respond": "done", "reply": "done",
        "final_answer": "done", "finish": "done",
    }

    def _resolve_tool_name(self, raw: str):
        """把模型传来的（可能不对的）工具名解析成真实注册名。
        返回 (resolved_name, mapped_note)。mapped_note 为空表示原名即合法。"""
        import difflib
        name = (raw or "").strip()
        if name in self._tools:
            return name, ""
        # 1) 显式别名
        mapped = self.TOOL_ALIASES.get(name.lower())
        if mapped is None:
            # 2) 模糊匹配（编辑距离）
            matches = difflib.get_close_matches(
                name.lower(), [t.lower() for t in self._tools], n=1, cutoff=0.6
            )
            if matches:
                for t in self._tools:
                    if t.lower() == matches[0]:
                        mapped = t
                        break
        if mapped is not None and mapped in self._tools:
            return mapped, f"工具名 '{raw}' 未注册，已自动映射到 '{mapped}'"
        return name, ""

    async def execute(self, tool_name: str, params: dict) -> "ExecutionResult":
        """异步执行工具（由 Commander 调用）"""
        import uuid
        resolved, note = self._resolve_tool_name(tool_name)
        if resolved not in self._tools:
            return ExecutionResult(id=str(uuid.uuid4()), status="error",
                                  error=f"未知工具: {tool_name}", tool=tool_name)
        fn = self._tools[resolved]["fn"]
        try:
            result = await fn(**params) if asyncio.iscoroutinefunction(fn) else fn(**params)
            out = str(result)
            if note:
                out = note + "\n" + out
            return ExecutionResult(id=str(uuid.uuid4()), status="success", output=out, tool=resolved)
        except BaseException as e:  # noqa: BLE001 - 见下方说明
            # 这里必须兜住 BaseException，不能只拦 Exception。
            # 实测事故：生成 PPT 时清理中间文件触发环境级删除拦截，对方抛的是
            # SystemExit（BaseException 的子类，不是 Exception）。
            # asyncio 的 Task.__step 对 SystemExit / KeyboardInterrupt 是
            # 「set_exception 后原样再 raise」，异常会一路穿透 run_forever，
            # 直接终结事件循环线程 —— 结果就是整个后端永久报废：
            # 之后每一条指令都只能返回「事件循环未运行」，必须重启进程才能恢复。
            # 工具出错只应该让这一步失败并回传给模型，绝不允许拖垮整个 Agent。
            if isinstance(e, (KeyboardInterrupt, SystemExit)) and sys.is_finalizing():
                raise
            exc_name = type(e).__name__
            return ExecutionResult(id=str(uuid.uuid4()), status="error",
                                   error=f"工具 {resolved} 执行失败（{exc_name}）: {e}",
                                   tool=resolved)


# ============================================================
# 主控制器 Commander
# ============================================================

class Commander:
    """
    Agent 主控制器（总工程师模式）
    - 对话管理：维护 session + 事件回调
    - 工具执行：调用工具注册表
    - 子代理管理：维护 SubAgentManager（任务状态 + 凭据共享）
    - 记忆管理：自动摘要 + 长期记忆（remember/recall）
    - 错误纠正：commander fix
    """

    def __init__(
        self,
        browser_manager,
        session: Optional[DeepSeekSession] = None,
        work_dir: str = "",
        on_event: Optional[Callable[[AgentEvent], None]] = None,
    ):
        self._bm = browser_manager
        self._session = session
        self._work_dir = work_dir or os.getcwd()
        self._on_event = on_event
        self._tools = ToolRegistry(self)
        self._subagent_manager = None  # 实际由 subagent_manager.get_subagent_manager 提供
        self._memory: Optional[MemoryManager] = None
        self._history_turns: int = 0
        self._accumulated_prompt: str = ""
        self._correction_pending: Optional[str] = None
        self._running = False
        self._interrupted = False  # 用户主动中断标志
        self._message_queue: list = []  # 插话消息队列
        self._protocol = Protocol()
        self._pending_attachments: list = []  # 待附带给 AI 平台的文件（图片/PDF等）

        # 先注册工具，再构建系统提示词（提示词依赖工具列表）
        self._register_tools()
        self._system_prompt = self._build_system_prompt()

    def _register_tools(self):
        """注册所有工具（包括子代理工具）"""
        import os
        from pathlib import Path

        # ─── 文件操作工具 ───
        async def file_write(**params):
            p = Path(params.get("path", ""))
            content = params.get("content", "")
            full_path = self._safe_path(p)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            # 写入后立即静态语法自检（不执行代码），有错则当轮回报给 AI 自行修正，
            # 避免「生成完就报成功、运行时才发现语法错」。
            try:
                from agent_core.syntax_check import check_file, format_result
                ok, msg = check_file(full_path, content)
                warn = format_result(full_path, ok, msg)
                if warn:
                    return f"文件已写入: {full_path}{warn}"
            except Exception:
                pass  # 校验器异常绝不阻断写入结果
            return f"文件已写入: {full_path}"

        async def file_read(**params):
            """读取文件（对标 OpenCode read，支持行范围）。"""
            p = Path(params.get("path", ""))
            full_path = self._safe_path(p)
            if not full_path.exists():
                return f"错误: 文件不存在 {full_path}"
            # PDF 文件用 PyPDF2 提取文本，其他文件用 UTF-8 文本读取
            # 【修复】_read_pdf 是这里的局部函数（带 self 参数），不是类方法，
            # 以前写成 self._read_pdf(...) → AttributeError，
            # AI 一读 PDF 就崩，然后开始疯狂自救（装库/写脚本）直到超时。
            if full_path.suffix.lower() == '.pdf':
                return await _read_pdf(self, full_path)
            text = full_path.read_text(encoding="utf-8")
            all_lines = text.split("\n")
            offset = int(params.get("offset", 1))
            limit = params.get("limit")
            if offset < 1:
                offset = 1
            seg = all_lines[offset - 1:]
            if limit is not None and int(limit) > 0:
                seg = seg[:int(limit)]
            numbered = "\n".join(f"{offset + i:>6}\t{ln}" for i, ln in enumerate(seg))
            return f"[文件 {full_path} 行 {offset}-{offset + len(seg) - 1} / 共 {len(all_lines)} 行]\n{numbered}"

        async def _read_pdf(self, path: Path) -> str:
            """使用 PyPDF2 读取 PDF 文件内容（PyPDF2 没装时回退 pypdf）"""
            reader_mod = None
            try:
                import PyPDF2 as reader_mod
            except ImportError:
                try:
                    import pypdf as reader_mod
                except ImportError:
                    return ("[错误] 读取 PDF 需要 PyPDF2 / pypdf，"
                            "请用项目解释器安装：\"D:/软件/Python/python.exe\" -m pip install PyPDF2")
            try:
                with open(path, 'rb') as f:
                    reader = reader_mod.PdfReader(f)
                    num_pages = len(reader.pages)
                    text_parts = []
                    for i, page in enumerate(reader.pages):
                        page_text = page.extract_text() or ""
                        if page_text.strip():
                            text_parts.append(f"--- 第 {i+1} 页 ---\n{page_text}")
                    if text_parts:
                        return f"[PDF 文件 {path.name} 共 {num_pages} 页]\n" + "\n\n".join(text_parts)
                    else:
                        return f"[PDF 文件 {path.name} 共 {num_pages} 页，但无法提取文本（可能是扫描版或图片型PDF）]"
            except ImportError:
                return f"[错误] PyPDF2 未安装，无法读取 PDF。请运行: pip install PyPDF2"
            except Exception as e:
                return f"[错误] 读取 PDF 失败: {e}"

        async def file_list(**params):
            p = Path(params.get("path", "."))
            full_path = self._safe_path(p)
            if not full_path.exists():
                return "目录不存在"
            items = list(full_path.iterdir())
            return "\n".join([f"{'[DIR]' if i.is_dir() else '[FILE]'} {i.name}" for i in items])

        async def dir_create(**params):
            raw = str(params.get("path", "")).strip().strip('"').strip("'").strip()
            if not raw:
                return "错误: 未提供 path"
            p = Path(raw)
            full_path = self._safe_path(p)
            full_path.mkdir(parents=True, exist_ok=True)
            return f"目录已创建: {full_path}"

        async def file_delete(**params):
            p = Path(params.get("path", ""))
            full_path = self._safe_path(p)
            if full_path.exists():
                if full_path.is_file():
                    full_path.unlink()
                else:
                    import shutil
                    shutil.rmtree(full_path)
                return f"已删除: {full_path}"
            return f"文件不存在: {full_path}"

        self._tools.register("file_write", "写入文件", file_write)
        self._tools.register("write", "写入文件(OpenCode 规范名, 同 file_write)", file_write)
        self._tools.register("file_read", "读取文件(支持 offset/limit 行范围)", file_read)
        self._tools.register("read", "读取文件(OpenCode 规范名, 同 file_read)", file_read)
        self._tools.register("file_list", "列出目录", file_list)
        self._tools.register("list", "列出目录(OpenCode 规范名, 同 file_list)", file_list)
        self._tools.register("dir_create", "创建目录", dir_create)
        self._tools.register("file_delete", "删除文件/目录", file_delete)

        # ─── 对标 OpenCode 文件编辑工具 ───
        async def file_edit(**params):
            """编辑文件（对标 OpenCode edit，支持多种模式）。
            params:
              path: 文件路径
              mode: 编辑模式，默认 "replace"
                    - "replace"（默认）: 把 old 字符串替换为 new；或 pattern/repl 正则替换
                    - "append": 在文件末尾追加 text（自动补换行）
                    - "insert": 在第 line 行(1-based)前插入 text；after=true 则在 line 行后插入
                    - "delete": 删除 [start,end] 行范围(1-based 闭区间)；也可配合 old 删除匹配行
              old / new: replace 模式的待替换文本
              pattern / repl: 正则替换（优先于 old/new）
              all: replace 正则模式下是否替换全部（默认仅第一处）
              text: append/insert 模式的插入文本
              line / start / end: 行号参数
            """
            import re

            def _syn_warn(target_path, text):
                """编辑完成后的语法自检提示；无问题返回空串，异常也静默返回空串。"""
                try:
                    from agent_core.syntax_check import check_file, format_result
                    okk, mm = check_file(target_path, text)
                    return format_result(target_path, okk, mm)
                except Exception:
                    return ""
            # 参数验证：必须提供 path，否则无法确定目标文件
            path = params.get("path")
            if not path:
                return "错误: file_edit 必须提供 path 参数"
            p = Path(path)
            full_path = self._safe_path(p)
            if not full_path.exists():
                return f"错误: 文件不存在 {full_path}"
            mode = (params.get("mode") or "replace").lower()
            # append/insert 模式必须提供 text
            if mode in ("append", "insert") and not params.get("text"):
                return f"错误: {mode} 模式必须提供 text 参数"
            raw = full_path.read_text(encoding="utf-8")
            lines = raw.split("\n")

            if mode == "replace":
                old = params.get("old")
                new = params.get("new", "")
                pattern = params.get("pattern")
                if pattern:
                    flags = re.MULTILINE
                    if params.get("dotall"):
                        flags |= re.DOTALL
                    count = 0 if params.get("all") else 1
                    text2, n = re.subn(pattern, params.get("repl", new), raw, count=count, flags=flags)
                    if n == 0:
                        return f"未找到匹配正则: {pattern}"
                else:
                    if old is None:
                        return "错误: 必须提供 old 或 pattern"
                    if old not in raw:
                        return f"错误: 未找到待替换文本: {old[:80]}"
                    n = raw.count(old)
                    if n > 1 and not params.get("all"):
                        return f"错误: old 出现 {n} 次，存在歧义，请补充上下文或加 all=true 全量替换"
                    text2 = raw.replace(old, new, 0) if params.get("all") else raw.replace(old, new, 1)
                full_path.write_text(text2, encoding="utf-8")
                return f"已编辑 {full_path}（替换 {n} 处，字符数 {len(raw)}→{len(text2)}）"

            elif mode == "append":
                text = params.get("text", "")
                content = (raw + "\n" if not raw.endswith("\n") and raw else raw) + text
                if not content.endswith("\n"):
                    content += "\n"
                full_path.write_text(content, encoding="utf-8")
                return f"已追加到 {full_path}（行数 {len(lines)}→{len(content.split(chr(10)))})"

            elif mode == "insert":
                text = params.get("text", "")
                lineno = int(params.get("line", len(lines) + 1))
                if lineno < 1:
                    lineno = 1
                if params.get("after"):
                    lineno = min(lineno + 1, len(lines) + 1)
                lines.insert(lineno - 1, text)
                full_path.write_text("\n".join(lines), encoding="utf-8")
                return f"已在第 {lineno} 行插入内容: {full_path}（行数 {len(raw.split(chr(10)))}→{len(lines)}）"

            elif mode == "delete":
                old = params.get("old")
                if old is not None:
                    kept = [ln for ln in lines if old not in ln]
                    removed = len(lines) - len(kept)
                    if removed == 0:
                        return f"未找到含待删文本的行: {old[:60]}"
                    full_path.write_text("\n".join(kept), encoding="utf-8")
                    return f"已删除 {removed} 行（含 '{old[:40]}'）: {full_path}"
                start = int(params.get("start", 1))
                end = int(params.get("end", start))
                if start < 1:
                    start = 1
                if end > len(lines):
                    end = len(lines)
                if start > end:
                    return f"错误: start({start}) > end({end})"
                del lines[start - 1:end]
                full_path.write_text("\n".join(lines), encoding="utf-8")
                return f"已删除第 {start}-{end} 行: {full_path}（行数 {len(raw.split(chr(10)))}→{len(lines)}）"

            else:
                return f"错误: 未知 mode={mode}（支持 replace/append/insert/delete）"

        async def grep(**params):
            """在目录中按正则/文本搜索文件内容（对标 opencode/rg 的代码搜索）。
            params:
              pattern: 搜索模式（正则）
              path: 搜索根目录，默认工作目录
              glob: 可选文件名过滤，如 '*.py'
              max_results: 可选，默认 50
              ignore_case: 可选 bool
            """
            import re
            root = self._safe_path(Path(params.get("path", ".")))
            pattern = params.get("pattern", "")
            if not pattern:
                return "错误: 必须提供 pattern"
            flags = re.IGNORECASE if params.get("ignore_case") else 0
            try:
                rx = re.compile(pattern, flags)
            except re.error as e:
                return f"正则错误: {e}"
            g = params.get("glob")
            max_results = int(params.get("max_results", 50))
            found = []
            try:
                it = root.rglob(g) if g else root.rglob("*")
            except Exception as e:
                return f"目录错误: {e}"
            for fp in it:
                if not fp.is_file():
                    continue
                if fp.stat().st_size > 5_000_000:
                    continue
                try:
                    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:
                    continue
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        found.append(f"{fp}:{i}: {line.strip()[:200]}")
                        if len(found) >= max_results:
                            break
                if len(found) >= max_results:
                    break
            if not found:
                return f"未找到匹配: {pattern}"
            return f"匹配 {len(found)} 处:\n" + "\n".join(found)

        async def web_fetch(**params):
            """抓取网页 URL 并提取可读正文文本（对标 workbuddy 的网页研究能力）。
            params:
              url: 目标网址
              timeout: 可选，默认 20s
            返回纯文本正文（已去除脚本/样式/标签噪声），失败返回错误信息。
            """
            import urllib.request
            import urllib.error
            import html as _html
            import re as _re
            url = params.get("url", "")
            if not url:
                return "错误: 必须提供 url"
            timeout = float(params.get("timeout", 20))
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "")
                    enc = "utf-8"
                    m = _re.search(r"charset=([\w-]+)", ctype)
                    if m:
                        enc = m.group(1)
                    body = raw.decode(enc, errors="ignore")
            except (urllib.error.URLError, OSError) as e:
                return f"抓取失败: {e}"
            # 去 <script>/<style>/<head>，再剥标签
            body = _re.sub(r"(?is)<script.*?</script>", " ", body)
            body = _re.sub(r"(?is)<style.*?</style>", " ", body)
            body = _re.sub(r"(?is)<head.*?</head>", " ", body)
            body = _re.sub(r"(?s)<[^>]+>", " ", body)
            body = _html.unescape(body)
            body = _re.sub(r"[ \t]+", " ", body)
            body = _re.sub(r"\n\s*\n+", "\n", body)
            text = body.strip()
            if len(text) > 20000:
                text = text[:20000] + "\n...[已截断]"
            return f"[来源 {url}]\n{text}"

        async def glob(**params):
            """按 glob 模式查找文件（对标 OpenCode glob，结果按修改时间排序）。
            params:
              pattern: 匹配模式，如 '*.py'、'**/*.py'、'src/**/*.ts'
              path: 搜索根目录，默认工作目录
              max_results: 最多返回数量，默认 200
            """
            import fnmatch
            pattern = params.get("pattern", "")
            if not pattern:
                return "错误: 必须提供 pattern"
            root = self._safe_path(Path(params.get("path", ".")))
            if not root.exists():
                return f"错误: 目录不存在 {root}"
            results = []
            try:
                for p in root.rglob("*"):
                    try:
                        rel = str(p.relative_to(root))
                    except Exception:
                        rel = str(p)
                    name = p.name
                    if (fnmatch.fnmatch(name, pattern)
                            or fnmatch.fnmatch(rel, pattern)
                            or fnmatch.fnmatch(str(p), pattern)):
                        try:
                            mtime = p.stat().st_mtime
                        except Exception:
                            mtime = 0
                        results.append((mtime, str(p)))
            except Exception as e:
                return f"glob 错误: {e}"
            results.sort(key=lambda x: -x[0])
            out = [f for _, f in results[:int(params.get("max_results", 200))]]
            if not out:
                return f"未匹配: {pattern}"
            return f"匹配 {len(out)} 个文件:\n" + "\n".join(out)

        async def websearch(**params):
            """网络搜索（对标 OpenCode websearch）。
            默认使用 DuckDuckGo HTML 解析；可在 agent_core/search_providers.json
            配置其它 provider（如带 API key 的搜索引擎）。
            params:
              query: 搜索词
              max_results: 最多结果，默认 8
              provider: 可选，覆盖默认 provider
            """
            query = params.get("query") or params.get("q") or ""
            if not query:
                return "错误: 必须提供 query"
            max_results = int(params.get("max_results", 8))
            import urllib.request, urllib.parse, urllib.error
            import html as _html, re as _re, json as _json
            # 可选 provider 配置
            provider = (params.get("provider") or "").strip()
            cfg_path = None
            try:
                from agent_core import xrz_paths
                cfg_path = xrz_paths.DATA_ROOT / "search_providers.json"
            except Exception:
                pass
            if cfg_path and cfg_path.exists():
                try:
                    provs = _json.loads(cfg_path.read_text(encoding="utf-8"))
                    prov = provs.get(provider or provs.get("default", ""), {})
                    if prov.get("url") and prov.get("parse") == "json":
                        return _websearch_json(query, prov, max_results)
                except Exception:
                    pass
            # 默认：DuckDuckGo HTML
            try:
                url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                with urllib.request.urlopen(req, timeout=float(params.get("timeout", 15))) as r:
                    page = r.read().decode("utf-8", "ignore")
                items = []
                for m in _re.finditer(
                        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, _re.S):
                    link = m.group(1)
                    title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    link = _decode_ddg(link)
                    items.append({"title": title, "link": link})
                snips = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, _re.S)
                for i, it in enumerate(items[:max_results]):
                    if i < len(snips):
                        it["snippet"] = _re.sub(r"<[^>]+>", "", snips[i]).strip()
                if not items:
                    return f"未找到搜索结果: {query}"
                lines = [f"搜索「{query}」共 {len(items)} 条:"]
                for i, it in enumerate(items[:max_results], 1):
                    lines.append(f"{i}. {it.get('title','')}\n   {it.get('link','')}\n   {it.get('snippet','')}")
                return "\n".join(lines)
            except (urllib.error.URLError, OSError) as e:
                return f"搜索失败: {e}（可检查网络或在 search_providers.json 配置带 API key 的 provider）"

        def _decode_ddg(link):
            """解码 DuckDuckGo 重定向链接中的真实 URL。"""
            import urllib.parse as _up
            m = _re.search(r"uddg=([^&]+)", link)
            if m:
                return _up.unquote(m.group(1))
            return link

        def _websearch_json(query, prov, max_results):
            import urllib.request, urllib.parse, urllib.error, json as _json, re as _re
            url = prov["url"] + urllib.parse.quote(query)
            headers = {"User-Agent": "Mozilla/5.0"}
            for k, v in (prov.get("headers") or {}).items():
                headers[k] = v
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = _json.loads(r.read().decode("utf-8", "ignore"))
                items = []
                for it in data[:max_results]:
                    items.append({
                        "title": str(it.get("title", "")),
                        "link": str(it.get("url") or it.get("link", "")),
                        "snippet": _re.sub(r"<[^>]+>", "", str(it.get("snippet") or it.get("description") or "")).strip(),
                    })
                if not items:
                    return f"未找到搜索结果: {query}"
                lines = [f"搜索「{query}」共 {len(items)} 条:"]
                for i, it in enumerate(items, 1):
                    lines.append(f"{i}. {it.get('title','')}\n   {it.get('link','')}\n   {it.get('snippet','')}")
                return "\n".join(lines)
            except Exception as e:
                return f"搜索失败: {e}"

        async def apply_patch(**params):
            """应用统一 diff 补丁（对标 opencode/codex 的 patch 应用）。
            params:
              patch: 统一 diff 文本（支持 --- a/.. +++ b/.. 前缀与 /dev/null 新建/删除文件）
              base_dir: 可选，补丁里相对路径的基准目录（默认工作目录）
            返回每个文件的应用结果摘要；冲突会逐文件报错而不中断其他文件。
            """
            import re as _re
            patch = params.get("patch", "")
            if not patch.strip():
                return "错误: 必须提供 patch"
            base = self._safe_path(Path(params.get("base_dir", ".")))

            def _norm(p):
                p = (p or "").strip()
                p = _re.sub(r"\t.*$", "", p)        # 去掉\t时间戳
                if p.startswith("a/") or p.startswith("b/"):
                    p = p[2:]
                if p in ("/dev/null", ""):
                    return None
                return p

            # 解析补丁为 文件 -> hunks
            files = []
            cur = None
            for raw in patch.split("\n"):
                if raw.startswith("--- "):
                    if cur:
                        files.append(cur)
                    cur = {"old": raw[4:], "new": None, "hunks": []}
                    continue
                if raw.startswith("+++ "):
                    if cur is not None:
                        cur["new"] = raw[4:]
                    continue
                if raw.startswith("@@"):
                    if cur is None:
                        continue
                    m = _re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
                    if not m:
                        continue
                    cur["hunks"].append({
                        "old_start": int(m.group(1)),
                        "new_start": int(m.group(3)),
                        "lines": [],
                    })
                    continue
                if raw.startswith("\\"):   # "\ No newline at end of file"
                    continue
                if cur is None or not cur["hunks"]:
                    continue
                if raw.startswith("+"):
                    cur["hunks"][-1]["lines"].append(("+", raw[1:]))
                elif raw.startswith("-"):
                    cur["hunks"][-1]["lines"].append(("-", raw[1:]))
                else:
                    cur["hunks"][-1]["lines"].append((" ", raw[1:] if raw.startswith(" ") else raw))
            if cur:
                files.append(cur)

            def _apply(f):
                old = _norm(f["old"]); new = _norm(f["new"])
                if old is None and new is None:
                    return "跳过: 路径无法解析"
                target_rel = new or old
                target = self._safe_path(base / target_rel)
                if new is None:   # 删除文件
                    if target.exists():
                        target.unlink()
                        return f"已删除: {target}"
                    return f"删除跳过(不存在): {target}"
                is_new = (old is None)
                if is_new:
                    # 新文件：原文件不存在，所有 + 行即为内容（忽略上下文/位置）
                    out = []
                    for h in f["hunks"]:
                        for tag, content in h["lines"]:
                            if tag == "+":
                                out.append(content)
                    _new_text = "\n".join(out)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(_new_text, encoding="utf-8")
                    return f"已应用(新建): {target} (行数 0→{len(out)})" + _syn_warn(target, _new_text)
                original = target.read_text(encoding="utf-8").split("\n") if target.exists() else []
                out = []
                oi = 0
                ok = True
                for h in f["hunks"]:
                    start = h["old_start"] - 1
                    if start > oi:
                        out.extend(original[oi:start]); oi = start
                    elif start < oi:
                        ok = False; break
                    for tag, content in h["lines"]:
                        if tag == " ":
                            out.append(content); oi += 1
                        elif tag == "-":
                            oi += 1
                        else:
                            out.append(content)
                if not ok:
                    return f"补丁冲突(行号错位): {target}"
                out.extend(original[oi:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("\n".join(out), encoding="utf-8")
                return f"已应用: {target} (行数 {len(original)}→{len(out)})"

            results = [_apply(f) for f in files]
            return "\n".join(results) if results else "错误: 补丁为空或格式无法识别"

        self._tools.register("file_edit", "编辑文件(replace/append/insert/delete, 对标OpenCode edit)", file_edit)
        self._tools.register("edit", "编辑文件(OpenCode 规范名, 同 file_edit)", file_edit)
        self._tools.register("grep", "按正则搜索文件内容(代码搜索, 对标OpenCode grep)", grep)
        self._tools.register("glob", "按模式查找文件(对标OpenCode glob, 如 **/*.py)", glob)
        self._tools.register("web_fetch", "抓取网页URL返回可读正文(对标OpenCode webfetch)", web_fetch)
        self._tools.register("webfetch", "抓取网页URL返回可读正文(OpenCode 规范名, 同 web_fetch)", web_fetch)
        self._tools.register("websearch", "网络搜索(DuckDuckGo, 对标OpenCode websearch)", websearch)
        self._tools.register("apply_patch", "应用统一diff补丁(对标OpenCode/Codex patch)", apply_patch)
        self._tools.register("patch", "应用统一diff补丁(OpenCode 规范名, 同 apply_patch)", apply_patch)

        # ─── 对标 OpenCode 的任务/权限/会话/MCP 框架 ───
        from agent_core import xrz_paths as _xz

        async def todowrite(**params):
            """任务列表管理（对标 OpenCode todowrite）。
            params（三选一）:
              todos: 任务数组整体替换（元素可为字符串，或 {content,status,activeForm}）
              content: 新增一条任务
              id: 更新指定任务（配合 content/activeForm/status）
            持久化到 DATA_ROOT/todos.json。"""
            import json as _json
            p = _xz.DATA_ROOT / "todos.json"
            todos = []
            if p.exists():
                try:
                    todos = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    todos = []
            if "todos" in params and isinstance(params["todos"], list):
                new = []
                for i, t in enumerate(params["todos"], 1):
                    if isinstance(t, str):
                        new.append({"id": i, "content": t, "status": "pending", "activeForm": t})
                    elif isinstance(t, dict):
                        new.append({"id": t.get("id", i), "content": t.get("content", ""),
                                    "status": t.get("status", "pending"),
                                    "activeForm": t.get("activeForm", t.get("content", ""))})
                todos = new
            elif params.get("content"):
                nid = max([t.get("id", 0) for t in todos], default=0) + 1
                todos.append({"id": nid, "content": params["content"], "status": "pending",
                              "activeForm": params.get("activeForm", params["content"])})
            elif params.get("id") is not None:
                tid = params["id"]
                for t in todos:
                    if t.get("id") == tid:
                        if "content" in params:
                            t["content"] = params["content"]
                        if "activeForm" in params:
                            t["activeForm"] = params["activeForm"]
                        if "status" in params:
                            t["status"] = params["status"]
                        break
            else:
                return "错误: 需提供 todos(整体替换) / content(新增) / id(更新)"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
            return _format_todos(todos)

        def _format_todos(todos):
            if not todos:
                return "任务列表为空（用 todowrite 添加任务）"
            done = sum(1 for t in todos if t.get("status") == "completed")
            lines = [f"任务列表 ({done}/{len(todos)} 完成):"]
            for t in todos:
                mark = {"completed": "✅", "in_progress": "🔄", "pending": "⬜"}.get(t.get("status", "pending"), "⬜")
                lines.append(f"  {mark} [{t.get('id')}] {t.get('content')}")
            return "\n".join(lines)

        async def todoread(**params):
            """读取当前任务列表（对标 OpenCode todoread）。"""
            import json as _json
            p = _xz.DATA_ROOT / "todos.json"
            if not p.exists():
                return _format_todos([])
            try:
                todos = _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                todos = []
            return _format_todos(todos)

        def _perm_path():
            return _xz.DATA_ROOT / "permissions.json"

        def _load_perms():
            import json as _json
            p = _perm_path()
            if p.exists():
                try:
                    return _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return {"default": "allow", "rules": []}

        async def permission_check(**params):
            """查询某工具/命令的权限（对标 OpenCode permission，支持通配符）。
            返回 {key, action}，action ∈ allow/deny/ask；未命中规则则用 default。"""
            import json as _json, fnmatch
            key = params.get("key") or params.get("tool") or ""
            if not key:
                return "错误: 必须提供 key"
            cfg = _load_perms()
            action = cfg.get("default", "allow")
            for rule in cfg.get("rules", []):
                rk = rule.get("key", "")
                if rk == key or fnmatch.fnmatch(key, rk):
                    action = rule.get("action", action)
            return _json.dumps({"key": key, "action": action}, ensure_ascii=False)

        async def permission_list(**params):
            """列出全部权限规则（对标 OpenCode permission）。"""
            import json as _json
            return _json.dumps(_load_perms(), ensure_ascii=False, indent=2)

        async def permission_set(**params):
            """设置一条权限规则（allow/deny/ask，支持通配符如 mcp_*、git *）。"""
            import json as _json
            key = params.get("key") or params.get("tool")
            action = params.get("action", "allow")
            if not key:
                return "错误: 必须提供 key"
            if action not in ("allow", "deny", "ask"):
                return "错误: action 必须是 allow/deny/ask"
            cfg = _load_perms()
            rules = cfg.setdefault("rules", [])
            found = False
            for r in rules:
                if r.get("key") == key:
                    r["action"] = action
                    found = True
                    break
            if not found:
                rules.append({"key": key, "action": action})
            p = _perm_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"已设置权限: {key} -> {action}"

        def _save_sessions(p, sessions):
            import json as _json
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")

        async def session_manager(**params):
            """多会话并行管理框架（对标 OpenCode 的 session/task 多会话能力）。
            action: list | current | create | switch
              create: name/platform/cwd 可选
              switch: id 指定会话
            会话元数据持久化到 DATA_ROOT/sessions.json，并广播 SESSION_UPDATED 事件。"""
            import json as _json
            action = (params.get("action") or "list").lower()
            p = _xz.DATA_ROOT / "sessions.json"
            sessions = []
            if p.exists():
                try:
                    sessions = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    sessions = []
            if action == "list":
                return _json.dumps({"sessions": sessions, "current": getattr(self, "_active_session_id", None)},
                                   ensure_ascii=False, indent=2)
            if action == "current":
                return _json.dumps({"current": getattr(self, "_active_session_id", None), "sessions": sessions},
                                   ensure_ascii=False)
            if action == "create":
                nid = max([s.get("id", 0) for s in sessions], default=0) + 1
                rec = {"id": nid, "name": params.get("name") or f"会话 {nid}",
                       "platform": params.get("platform", "deepseek"), "cwd": params.get("cwd", ""),
                       "created": time.time()}
                sessions.append(rec)
                self._active_session_id = nid
                _save_sessions(p, sessions)
                self._emit(EventType.SESSION_UPDATED, {"sessions": sessions, "current": nid})
                return _json.dumps(rec, ensure_ascii=False)
            if action == "switch":
                nid = params.get("id")
                for s in sessions:
                    if s.get("id") == nid:
                        self._active_session_id = nid
                        _save_sessions(p, sessions)
                        self._emit(EventType.SESSION_UPDATED, {"sessions": sessions, "current": nid})
                        return f"已切换到会话 {nid}: {s.get('name')}"
                return f"错误: 未找到会话 {nid}"
            if action == "delete":
                nid = params.get("id")
                sessions = [s for s in sessions if s.get("id") != nid]
                _save_sessions(p, sessions)
                self._emit(EventType.SESSION_UPDATED, {"sessions": sessions, "current": None})
                return f"已删除会话 {nid}"
            return "错误: 未知 action(支持 list/current/create/switch/delete)"

        async def task(**params):
            """委派子代理执行任务（对标 OpenCode task）。
            params: goal(必填) / type(research|visit|coding)
            子代理跟随【当前对话平台】：用当前 session 的浏览器开子窗口，
            平台浏览器用 PlatformSession 适配（接口与 DeepSeekSession 对齐）。"""
            goal = params.get("goal") or params.get("prompt") or ""
            if not goal:
                return "错误: 必须提供 goal"
            try:
                from agent_core.subagent_manager import get_subagent_manager
                mgr = get_subagent_manager(self._work_dir)
                # 【子代理跟随当前平台】优先用当前 session 的浏览器（self._session._bm，
                # 跟随用户当前活跃平台：通义/豆包/元宝/DeepSeek），回退到构造时的 self._bm。
                # 以前注入固定的 self._bm（DeepSeek 浏览器）→ 用户在通义发任务，子代理却
                # 跑去 DeepSeek 网页，平台错位。现在子代理在哪个平台就开哪个平台的子窗口。
                cur_bm = None
                try:
                    cur_bm = getattr(self._session, "_bm", None)
                except Exception:
                    cur_bm = None
                if cur_bm is None:
                    cur_bm = self._bm
                # 派发前注入母浏览器 + 事件回调（同 _browser_research/_browser_visit）。
                # spawn_child 现在 DeepSeek BrowserManager 与 PlatformBrowserManager 都有，
                # 故任意平台都能派生子窗口。
                if cur_bm is not None and hasattr(cur_bm, "spawn_child"):
                    mgr.set_browser_manager(cur_bm)
                    # 子代理内部多轮工具调用带 subagent_task_id 标记冒泡到 GUI。
                    try:
                        mgr.set_event_forwarder(self._on_event)
                    except Exception:
                        pass
                    plat = getattr(getattr(cur_bm, "profile", None), "name",
                                   getattr(cur_bm, "platform_key", "")) or type(cur_bm).__name__
                    tid = await mgr.spawn_subagent(goal, task_type=params.get("type", "research"))
                    return ("已委派子代理任务: id=%s\n目标: %s\n（平台: %s，"
                            "用 check_task / wait_task 查询结果）" % (tid, goal, plat))
                return ("错误: 当前平台浏览器不支持派生子窗口（缺 spawn_child 方法），"
                        "无法启动子代理。")
            except Exception as e:
                return f"委派子代理失败: {e}"

        async def mcp_client(**params):
            """MCP 协议客户端框架（stdio 传输，对标 OpenCode 的 MCP 工具）。
            action:
              list_servers                      列出 mcp.json 中配置的 server
              list_tools  server=<名>           启动 server 并列出其 tools
              call_tool   server=<名> tool=<名> args=<obj>  调用某 tool
            配置：DATA_ROOT/mcp.json -> {"servers": {"名": {"command","args","env"}}}"""
            import json as _json
            action = (params.get("action") or "list_servers").lower()
            server = params.get("server", "")
            cfg_path = _xz.DATA_ROOT / "mcp.json"
            if not cfg_path.exists():
                return ("未配置 MCP：请在 " + str(cfg_path) + " 配置 servers，如：\n"
                        '{"servers": {"my": {"command": "npx", "args": ["-y", '
                        '"@modelcontextprotocol/server-everything"]}}}')
            try:
                cfgs = _json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as e:
                return f"mcp.json 解析失败: {e}"
            servers = cfgs.get("servers", {})
            if action == "list_servers":
                return _json.dumps({"servers": list(servers.keys())}, ensure_ascii=False)
            if server not in servers:
                return f"未知 MCP server: {server}（可用: {list(servers.keys())}）"
            spec = servers[server]
            try:
                proc = await asyncio.create_subprocess_exec(
                    spec["command"], *spec.get("args", []),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, **(spec.get("env") or {})})
            except Exception as e:
                return f"启动 MCP server 失败: {e}"
            counter = [0]

            async def _rpc(method, pms=None, notif=False):
                counter[0] += 1
                i = counter[0]
                msg = _json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": pms or {}})
                proc.stdin.write((msg + "\n").encode())
                await proc.stdin.drain()
                if notif:
                    return None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        return {"error": {"message": "MCP server 关闭连接"}}
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = _json.loads(line)
                    except Exception:
                        continue
                    if r.get("id") == i:
                        return r

            try:
                init = await _rpc("initialize", {"protocolVersion": "2024-11-05",
                                                 "capabilities": {}, "clientInfo": {"name": "xrz", "version": "1.0"}})
                await _rpc("notifications/initialized", {}, notif=True)
                if action == "list_tools":
                    res = await _rpc("tools/list", {})
                    tools = (res or {}).get("result", {}).get("tools", [])
                    return _json.dumps({"server": server, "tools": tools}, ensure_ascii=False)
                if action == "call_tool":
                    tname = params.get("tool", "")
                    targs = params.get("args") or {}
                    res = await _rpc("tools/call", {"name": tname, "arguments": targs})
                    return _json.dumps({"server": server, "tool": tname,
                                        "result": (res or {}).get("result")}, ensure_ascii=False)
                return _json.dumps({"server": server,
                                    "initialize": (init or {}).get("result")}, ensure_ascii=False)
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass

        self._tools.register("todowrite", "任务列表创建/更新/进度(对标OpenCode todowrite)", todowrite)
        self._tools.register("todoread", "读取任务列表(对标OpenCode todoread)", todoread)
        # 注：ask / question 工具已【刻意禁用】——agent 不得把任务踢回给用户，遇到不确定
        # 必须自己选合理默认方案用真实工具推进到底（见 _execute_command 中的拦截逻辑）。
        self._tools.register("permission_check", "权限查询(allow/deny/ask+通配符)", permission_check)
        self._tools.register("permission_list", "列出权限规则", permission_list)
        self._tools.register("permission_set", "设置权限规则(allow/deny/ask+通配符)", permission_set)
        self._tools.register("session_manager", "多会话并行管理框架", session_manager)
        self._tools.register("task", "委派子代理执行任务(对标OpenCode task)", task)
        self._tools.register("mcp_client", "MCP协议客户端框架(stdio)", mcp_client)

        # ─── 任务清理工具 ───
        async def cleanup_manager(**params):
            """任务清理框架
            action: clean_all - 清理所有过期任务和附件
            """
            import shutil as _shutil
            import time as _time
            from pathlib import Path as _Path

            action = (params.get("action") or "clean_all").lower()
            data_root = _Path(_xz.DATA_ROOT)
            summaries = []

            if action == "clean_all":
                # 1. 清理空目录
                gui_session = data_root / "XianRenZhang_tasks" / "gui_session"
                if gui_session.exists():
                    for d in gui_session.iterdir():
                        if d.is_dir() and not any(d.iterdir()) and d.name != "memory":
                            try:
                                d.rmdir()
                                summaries.append(f"删除空目录: {d.name}")
                            except BaseException:  # noqa: BLE001 清理失败无所谓，但不能让异常逃逸
                                pass

                # 2. 清理超7天的旧附件
                attachments = data_root / "gui_attachments"
                if attachments.exists():
                    cleaned = 0
                    for f in attachments.iterdir():
                        if f.is_file() and _time.time() - f.stat().st_mtime > 7 * 86400:
                            try:
                                f.unlink()
                                cleaned += 1
                            except Exception:
                                pass
                    if cleaned:
                        summaries.append(f"删除 {cleaned} 个旧附件（超过7天）")

                # 3. 清理超时7天的子代理结果
                if gui_session.exists():
                    sub_cleaned = 0
                    for d in gui_session.glob("subagent_*"):
                        if d.is_dir():
                            result_file = d / "result.json"
                            if result_file.exists():
                                try:
                                    if _time.time() - result_file.stat().st_mtime > 7 * 86400:
                                        _shutil.rmtree(str(d), ignore_errors=True)
                                        sub_cleaned += 1
                                except Exception:
                                    pass
                    if sub_cleaned:
                        summaries.append(f"删除 {sub_cleaned} 个过期子代理任务")

                # 4. 空间统计
                total = sum(f.stat().st_size for f in data_root.rglob("*") if f.is_file())
                summaries.append(f"数据目录总大小: {total / 1024 / 1024:.1f} MB")

            return "\n".join(summaries) if summaries else "无需清理"
        self._tools.register("cleanup_manager", "任务清理管理框架", cleanup_manager)

        async def attach_file(**params):
            """把本地文件（图片/PDF 等）附加到下次发送给 AI 平台的消息里。

            params.paths: 文件绝对/相对路径，支持单路径或路径列表。
            文件会被暂存，在下一轮对话时自动上传到输入框（出现预览/缩略图）。
            """
            paths = params.get("paths", [])
            if isinstance(paths, str):
                paths = [paths]
            if not paths:
                return "错误：paths 为空"
            from pathlib import Path as _P
            ok, missing = [], []
            for p in paths:
                if _P(p).exists():
                    ok.append(str(_P(p)))
                else:
                    missing.append(p)
            if not ok:
                return f"错误：所有文件都不存在：{missing}"
            self._pending_attachments.extend(ok)
            msg = f"已就绪附件 {len(ok)} 个"
            if missing:
                msg += f"（{len(missing)} 个不存在已跳过：{missing}）"
            msg += "，将在下一轮对话时自动附带发送给 AI"
            return msg

        # 【已取消】set_deep_think —— 不再注册给 AI。
        # 理由（用户决定）：深度思考是本软件的【界面开关】，由用户自己点，
        # 不该交给 AI 去「猜按钮再点」。实测四个平台上 AI 去点这个开关从未成功过，
        # 反而把「深度思考 开启/关闭」当成一条普通任务去执行，浪费轮次还污染历史。
        # self._tools.register("set_deep_think", ...)
        self._tools.register("attach_file", "附加文件(图片/PDF等)到下次对话(支持paths列表)", attach_file)

        # ─── Shell 执行工具 ───
        async def shell_exec(**params):
            import asyncio
            cmd = params.get("command", "")
            # 注意：不再硬拦截「写 C 盘」的命令行——用户若明确要求在某个路径生成，
            # 就按用户路径执行，真实系统错误（如权限/路径不存在）会原样返回，
            # 由 AI 自行判断并纠正，脚本不替用户做路径决定。
            if not str(cmd).strip():
                return ("[系统] 收到空的 shell 命令，未执行任何操作。"
                        "请不要重复发送空命令；如需创建文件请改用 file_write 工具，"
                        "任务已完成时请调用 done()。")
            timeout = params.get("timeout", 60)

            def _dec(b: bytes) -> str:
                """Windows 控制台默认 GBK/CP936 输出，直接用 utf-8 解码会变成乱码，
                模型看不懂乱码就会反复重发同一条命令 → 死循环。这里做多编码回退。"""
                if not b:
                    return ""
                for enc in ("utf-8", "gbk", "cp936", "mbcs"):
                    try:
                        return b.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return b.decode("utf-8", errors="replace")

            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return f"[退出码 {proc.returncode}]\n{_dec(stdout)}\n{_dec(stderr)}"
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return f"命令超时（>{timeout}s）"
            except Exception as e:
                return f"执行错误: {e}"

        self._tools.register("shell_exec", "执行Shell命令", shell_exec)
        self._tools.register("bash", "执行Shell命令(OpenCode 规范名, 同 shell_exec)", shell_exec)

        # ─── 浏览器操作工具（母代理直接执行）───

        async def browser_click(**params):
            return await self._bm.browser_click(params.get("selector", ""))

        async def browser_fill(**params):
            return await self._bm.browser_fill(params.get("selector", ""), params.get("text", ""))

        async def browser_screenshot(**params):
            path = params.get("path", "screenshot.png")
            full_path = Path(self._work_dir) / path
            return await self._bm.browser_screenshot(str(full_path))

        async def browser_search(**params):
            """网页搜索工具 - 不使用浏览器，直接用 HTTP 请求抓取页面
            
            通过 Bing 搜索获取结果，抓取页面内容后返回给 AI。
            不会占用母代理的浏览器状态。
            """
            import subprocess
            import json
            from pathlib import Path
            
            query = params.get("query", "")
            max_pages = params.get("max_pages", 3)
            output_file = params.get("output_file", "")
            
            if not query:
                return "[错误] 缺少 query 参数"
            
            # 确定输出文件路径
            if not output_file:
                output_file = str(Path(self._work_dir) / "search_results.json")
            
            # 调用网页搜索脚本
            script_path = Path(__file__).parent.parent / "web_searcher.py"
            
            try:
                result = subprocess.run(
                    [sys.executable, str(script_path), query, str(max_pages), output_file],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    # 读取结果文件
                    if Path(output_file).exists():
                        with open(output_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        return f"[搜索完成] 抓取 {data.get('scraped_count', 0)} 个页面\n\n{data.get('findings', '')}"
                    else:
                        return result.stdout
                else:
                    return f"[搜索错误] {result.stderr}"
                    
            except subprocess.TimeoutExpired:
                return "[错误] 搜索超时（60秒）"
            except Exception as e:
                return f"[错误] {str(e)}"
        self._tools.register("browser_click", "点击元素", browser_click)
        self._tools.register("browser_fill", "填写输入框", browser_fill)
        self._tools.register("browser_screenshot", "截图", browser_screenshot)
        self._tools.register("browser_search", "搜索", browser_search)

        # ─── 研究代理工具（独立子代理进程）───
        async def _browser_research(**params):
            """启动研究子代理（同一浏览器、不同窗口，共享登录态）并等待完成"""
            from .subagent_manager import get_subagent_manager, _SUBAGENT_DEPTH
            sam = get_subagent_manager(self._work_dir)
            # 把母代理的浏览器管理器注入，子代理将在同一浏览器里开新窗口
            sam.set_browser_manager(self._bm)

            query = params.get("query", "")
            max_pages = params.get("max_pages", 5)
            timeout = params.get("timeout", 600)

            # 构建完整查询
            full_query = f"深度研究: {query} (最多访问 {max_pages} 个页面)"

            # 子代理场景：如果已达到嵌套上限，直接在当前窗口执行搜索而非派生子代理
            if _SUBAGENT_DEPTH >= 1:
                # 子代理模式下，直接使用 browser_search 工具执行
                return await self._tools.execute("browser_search", {"query": query, "max_pages": max_pages})

            # 启动子代理（非阻塞，进程内、同浏览器）
            task_id = await sam.spawn_subagent(full_query, task_type="research")

            # 母代理等待子代理完成，确保不提前返回
            task = await sam.wait_task(task_id, timeout=timeout)
            if task and task.result and task.result.success:
                r = task.result
                parts = [f"✅ 研究任务完成！"]
                if r.findings:
                    parts.append(f"\n=== 核心发现 ===\n{r.findings[:3000]}")
                if r.output:
                    parts.append(f"\n=== 详细回复 ===\n{r.output[:1500]}")
                if r.files:
                    parts.append(f"\n=== 产物文件 ({len(r.files)} 个) ===")
                    for fi in r.files[:10]:
                        name = fi.get("name", "?")
                        size = fi.get("size", 0)
                        parts.append(f"  - {name} ({size} bytes)")
                return "\n".join(parts)
            else:
                error = task.result.error if task and task.result else "未知错误"
                return f"❌ 研究任务失败: {error}"

        async def _browser_visit(**params):
            """启动访问子代理（同一浏览器、不同窗口，共享登录态）并等待完成"""
            from .subagent_manager import get_subagent_manager, _SUBAGENT_DEPTH
            sam = get_subagent_manager(self._work_dir)
            # 把母代理的浏览器管理器注入，子代理将在同一浏览器里开新窗口
            sam.set_browser_manager(self._bm)

            url = params.get("url", "")
            timeout = params.get("timeout", 300)

            # 构建完整查询
            full_query = f"访问并分析网页: {url}"

            # 子代理场景：如果已达到嵌套上限，直接访问页面
            if _SUBAGENT_DEPTH >= 1:
                # 直接导航到 URL 并截图
                await self._bm.navigate_to(url)
                screenshot = await self._bm.take_screenshot()
                return f"[已访问] {url}\n截图: {screenshot}"

            # 启动子代理（非阻塞，进程内、同浏览器）
            task_id = await sam.spawn_subagent(full_query, task_type="visit")

            # 母代理等待子代理完成
            task = await sam.wait_task(task_id, timeout=timeout)
            if task and task.result and task.result.success:
                r = task.result
                parts = [f"✅ 网页访问完成！"]
                if r.findings:
                    parts.append(f"\n=== 页面内容 ===\n{r.findings[:3000]}")
                if r.output:
                    parts.append(f"\n=== 详细分析 ===\n{r.output[:1500]}")
                return "\n".join(parts)
            else:
                error = task.result.error if task and task.result else "未知错误"
                return f"❌ 网页访问失败: {error}"
        
        async def _check_task(**params):
            """检查子代理任务状态（含详细结果）"""
            from .subagent_manager import get_subagent_manager
            sam = get_subagent_manager(self._work_dir)

            task_id = params.get("task_id", "")
            task = sam.check_task(task_id)

            if not task:
                return f"任务 {task_id} 不存在"

            status_info = (
                f"任务: {task_id}\n"
                f"状态: {task.status.value}\n"
                f"类型: {task.task_type}\n"
                f"查询: {task.query[:80]}"
            )

            if task.result:
                r = task.result
                status_info += f"\n\n=== 结果 ===\n成功: {r.success}"
                if r.error:
                    status_info += f"\n错误: {r.error}"
                # findings：子代理的核心发现/产出内容（最重要）
                if r.findings:
                    findings_preview = r.findings[:1000] + "..." if len(r.findings) > 1000 else r.findings
                    status_info += f"\n\n--- 核心发现 ---\n{findings_preview}"
                # output：子代理最终回复文本
                if r.output:
                    output_preview = r.output[:500] + "..." if len(r.output) > 500 else r.output
                    status_info += f"\n\n--- 回复文本 ---\n{output_preview}"
                # 文件列表（带路径和大小）
                if r.files:
                    status_info += f"\n\n--- 产物文件 ({len(r.files)} 个) ---"
                    for fi in r.files[:10]:
                        name = fi.get("name", fi.get("path", "?"))
                        size = fi.get("size", 0)
                        preview = fi.get("preview", "")[:120] if fi.get("preview") else ""
                        status_info += f"\n  [{name}] ({size} bytes)"
                        if preview:
                            status_info += f" 预览: {preview}"

            return status_info

        async def _wait_task(**params):
            """等待子代理任务完成（返回完整结果）"""
            from .subagent_manager import get_subagent_manager
            sam = get_subagent_manager(self._work_dir)

            task_id = params.get("task_id", "")
            # 子代理内部多轮（搜索/抓取/写文件）常跑 5-8 分钟，默认 300s 会在
            # 子代理写 result.json 之前就超时 → 母代理拿到"未知错误"就弃用子代理
            # 结果去走常识兜底（实测 9/15：子代理其实 success=True，只是 wait 太急）。
            # 提到 900s，让 wait 等到子代理真正跑完再取结果。
            timeout = params.get("timeout", 900)

            task = await sam.wait_task(task_id, timeout=timeout)

            if not task:
                return f"等待任务 {task_id} 超时或任务不存在"

            if task.result and task.result.success:
                r = task.result
                parts = [f"任务 {task_id} 完成！"]
                if r.findings:
                    findings_text = r.findings[:2000] + "..." if len(r.findings) > 2000 else r.findings
                    parts.append(f"\n=== 核心发现/产出 ===\n{findings_text}")
                if r.output:
                    output_text = r.output[:800] + "..." if len(r.output) > 800 else r.output
                    parts.append(f"\n=== 子代理回复 ===\n{output_text}")
                if r.files:
                    parts.append(f"\n=== 产物文件 ({len(r.files)} 个) ===")
                    for fi in r.files[:15]:
                        name = fi.get("name", fi.get("path", "?"))
                        parts.append(f"  - {name} ({fi.get('size', 0)} bytes)")
                return "\n".join(parts)
            else:
                error = task.result.error if task.result else "未知错误"
                return f"任务 {task_id} 失败: {error}"

        self._tools.register("browser_research", "启动研究子代理（独立进程）", _browser_research)
        self._tools.register("browser_visit", "启动访问子代理（独立进程）", _browser_visit)
        self._tools.register("check_task", "检查子代理任务状态", _check_task)
        self._tools.register("wait_task", "等待子代理任务完成", _wait_task)

        # ─── Word 文档生成工具（母代理直接执行）───
        async def docx_tool_fn(**params):
            import os
            from pathlib import Path

            raw_content = ""
            for key in ["content", "text", "body", "data", "value", "raw"]:
                if key in params and params[key] is not None:
                    raw_content = params[key]
                    break

            # 清洗 DeepSeek/通义等网页 UI 残留：代码围栏、『json 复制 下载』『复制 下载』等
            # 工具栏文字会混进模型输出的内容里，必须剥掉，否则生成的文档满是垃圾行。
            def _clean_docx_text(t: str) -> str:
                import re
                out = []
                for ln in str(t).splitlines():
                    s = ln.rstrip()
                    if s.strip().startswith("```"):
                        continue
                    s = re.sub(r"^json\s*复制\s*下载\s*$", "", s)
                    s = re.sub(r"复制\s*下载\s*$", "", s)
                    s = re.sub(r"^\s*下载\s*$", "", s)
                    if s.strip():
                        out.append(s)
                return "\n".join(out)

            if isinstance(raw_content, str):
                raw_content = _clean_docx_text(raw_content)

            requested_filename = params.get("filename")
            filename = os.path.expandvars(str(requested_filename or "report.docx"))
            path = os.path.expandvars(str(params.get("path") or ""))
            filename_path = Path(filename)
            if filename_path.suffix.lower() != ".docx":
                filename_path = filename_path.with_suffix(".docx")

            # AI 有时把完整文件名误放在 path 中；此时不能再拼 report.docx，
            # 否则会产生 xxx.docx/report.docx 这种错误目录。
            if path and Path(path).suffix.lower() == ".docx" and not requested_filename:
                out_path = self._safe_path(Path(path))
            elif path:
                out_path = self._safe_path(Path(path) / filename_path)
            else:
                out_path = self._safe_path(filename_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                from docx import Document

                doc = Document()
                structured = isinstance(raw_content, dict) and isinstance(raw_content.get("sections"), list)
                if structured:
                    title = str(raw_content.get("title") or raw_content.get("filename") or out_path.stem)
                else:
                    # 非结构化：优先用内容里第一个 # 标题当文档标题，否则回退文件名
                    _first_h = ""
                    if isinstance(raw_content, str):
                        for _ln in raw_content.splitlines():
                            if _ln.strip().startswith("# "):
                                _first_h = _ln.strip()[2:].strip()
                                break
                    title = _first_h or out_path.stem
                doc.add_heading(title, 0)

                def add_content(value, *, list_style=None):
                    if value is None:
                        return
                    if isinstance(value, dict):
                        for key, item in value.items():
                            doc.add_heading(str(key), level=2)
                            add_content(item)
                        return
                    if isinstance(value, (list, tuple)):
                        for item in value:
                            add_content(item, list_style="List Bullet")
                        return
                    for raw_line in str(value).splitlines() or [""]:
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("### "):
                            doc.add_heading(line[4:], level=3)
                        elif line.startswith("## "):
                            doc.add_heading(line[3:], level=2)
                        elif line.startswith("# "):
                            doc.add_heading(line[2:], level=1)
                        elif line.startswith(("- ", "* ")):
                            doc.add_paragraph(line[2:], style="List Bullet")
                        else:
                            doc.add_paragraph(line, style=list_style)

                if structured:
                    for index, section in enumerate(raw_content["sections"], start=1):
                        if isinstance(section, dict):
                            heading = section.get("heading") or section.get("title") or f"第{index}节"
                            section_content = section.get("content", section.get("body", section.get("text", "")))
                        else:
                            heading = f"第{index}节"
                            section_content = section
                        doc.add_heading(str(heading), level=1)
                        add_content(section_content)
                elif isinstance(raw_content, dict):
                    add_content(raw_content)
                else:
                    add_content(raw_content)

                doc.save(str(out_path))
                return f"✅ Word 文档已生成: {out_path}"
            except ImportError as ie:
                raise ImportError("❌ python-docx 未安装。请运行：pip install python-docx") from ie
            except Exception as e:
                raise Exception(f"❌ Word 文档生成失败 ({type(e).__name__}): {e}") from e

        self._tools.register("docx_create", "生成 Word 文档（python-docx）", docx_tool_fn)

        # ─── PPT 生成工具 ───
        async def pptx_tool_fn(**params):
            import os
            from pathlib import Path

            raw_content = ""
            for key in ["content", "text", "body", "data", "value", "raw"]:
                if key in params and params[key] is not None:
                    raw_content = params[key]
                    break

            # 【实测事故】只传 path、不传内容时，旧代码会默默产出一个
            # 「封面(用文件名当标题) + Thank You 结尾」的空壳 2 页 PPT，
            # 用户要的「综合测试/功能展示/谢谢」三页一页都没有，
            # 但工具返回 success，模型还汇报「生成成功」，问题极难发现。
            # 这里直接报错，让模型补上内容重试，绝不允许静默生成空壳。
            _empty_content = (
                raw_content is None
                or (isinstance(raw_content, str) and not raw_content.strip())
                or (isinstance(raw_content, dict)
                    and not (raw_content.get("slides") or raw_content.get("content")
                             or raw_content.get("text")))
                or (isinstance(raw_content, (list, tuple)) and not raw_content)
            )
            if _empty_content:
                return ("错误：pptx_create 缺少内容，已取消生成（避免产出空壳 PPT）。\n"
                        "请在 params 里带上 content，例如：\n"
                        "  content='第1页「综合测试」\\n第2页「功能展示」\\n第3页「谢谢」'\n"
                        "也支持 Markdown（# 一级标题分段）或结构化 "
                        "{\"title\":\"...\",\"slides\":[{\"heading\":\"...\",\"items\":[...]}]}。\n"
                        "只给 path 是不够的，请补充内容后重新调用。")

            requested_filename = params.get("filename")
            filename = os.path.expandvars(str(requested_filename or "slides.pptx"))
            path = os.path.expandvars(str(params.get("path") or ""))
            filename_path = Path(filename)
            if filename_path.suffix.lower() != ".pptx":
                filename_path = filename_path.with_suffix(".pptx")

            if path and Path(path).suffix.lower() == ".pptx" and not requested_filename:
                out_path = self._safe_path(Path(path))
            elif path:
                out_path = self._safe_path(Path(path) / filename_path)
            else:
                out_path = self._safe_path(filename_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                # 走模板化构建器（agent_core/pptx_builder.py）：
                #  - 统一配色/版式主题，自动生成封面页 + 内容页 + 章节页
                #  - 智能分页：结构化 dict / Markdown / 纯文本都能拆成多页，
                #    杜绝旧实现「所有内容塞进 1 页」和「首行被当标题吞掉」的问题
                from agent_core.pptx_builder import build_pptx

                structured = isinstance(raw_content, dict) and isinstance(raw_content.get("slides"), list)
                deck_title = (
                    str(raw_content.get("title") or raw_content.get("filename") or out_path.stem)
                    if structured else out_path.stem
                )
                # 主题：允许 AI 通过 theme 指定，默认蓝色
                theme_name = str(params.get("theme") or "blue").lower()
                subtitle = str(params.get("subtitle") or "")

                info = build_pptx(
                    out_path,
                    raw_content,
                    deck_title=deck_title,
                    theme_name=theme_name,
                    subtitle=subtitle,
                )
                # 把实际生成的页标题回传：模型能一眼核对「我要的 3 页在不在」，
                # 避免它拿到一句「已生成」就以为万事大吉（实测过：模型用更差的
                # 内容重复调用，把已经生成好的 3 页覆盖成标题是文件名的空壳）。
                _titles = info.get("titles") or []
                _titles_s = " / ".join(_titles) if _titles else "（标题为空，请检查 content）"
                return (
                    f"✅ PPT 已生成: {info['path']}\n"
                    f"   共 {info['slides']} 页（含封面），主题: {info['theme']}，16:9 宽屏\n"
                    f"   各页标题: {_titles_s}\n"
                    f"   若页数或标题与要求不符，请带上完整内容重新调用（不要只传 path）。"
                )
            except ImportError as ie:
                msg = str(ie)
                if "pptx" in msg.lower():
                    raise ImportError("❌ python-pptx 未安装。请运行：pip install python-pptx") from ie
                raise
            except Exception as e:
                raise Exception(f"❌ PPT 生成失败 ({type(e).__name__}): {e}") from e

        self._tools.register(
            "pptx_create",
            "生成 PPT（python-pptx）。content 必须【逐页】给出，格式："
            "第1页「标题一」\\n第2页「标题二」\\n第3页「谢谢」，每页可另起行写要点；"
            "这样页数和标题才会与要求一一对应。只传 path 或传无分页的一大段文字，"
            "会导致页数对不上或被压成一页。",
            pptx_tool_fn)

        # ─── PDF 生成工具（reportlab）───
        async def pdf_tool_fn(**params):
            import os
            from pathlib import Path

            raw_content = ""
            for key in ["content", "text", "body", "data", "value", "raw"]:
                if key in params and params[key] is not None:
                    raw_content = params[key]
                    break

            # 与 pptx 一致：空内容直接报错，绝不出空壳 PDF
            _empty = (
                raw_content is None
                or (isinstance(raw_content, str) and not raw_content.strip())
                or (isinstance(raw_content, (list, tuple, dict)) and not raw_content)
            )
            if _empty:
                return ("错误：pdf_create 缺少内容，已取消生成（避免产出空壳 PDF）。\n"
                        "请在 params 里带上 content（纯文本或 Markdown：# 标题 / - 要点）后重试。")

            requested_filename = params.get("filename")
            filename = os.path.expandvars(str(requested_filename or "document.pdf"))
            path = os.path.expandvars(str(params.get("path") or ""))
            filename_path = Path(filename)
            if filename_path.suffix.lower() != ".pdf":
                filename_path = filename_path.with_suffix(".pdf")

            if path and Path(path).suffix.lower() == ".pdf" and not requested_filename:
                out_path = self._safe_path(Path(path))
            elif path:
                out_path = self._safe_path(Path(path) / filename_path)
            else:
                out_path = self._safe_path(filename_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.lib.units import cm
                from reportlab.lib.colors import HexColor
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.ttfonts import TTFont
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import ParagraphStyle

                # 中文字体：优先系统自带 SimSun / SimHei（D 盘环境 Windows 必有），
                # 注册失败则退回 Helvetica（中文变方块，但页面结构保留）。
                font_name = "Helvetica"
                try:
                    pdfmetrics.registerFont(TTFont("XRZSimSun", r"C:\Windows\Fonts\simsun.ttc"))
                    font_name = "XRZSimSun"
                except Exception:
                    try:
                        pdfmetrics.registerFont(TTFont("XRZSimHei", r"C:\Windows\Fonts\simhei.ttf"))
                        font_name = "XRZSimHei"
                    except Exception:
                        font_name = "Helvetica"

                title_style = ParagraphStyle("xrz_title", fontName=font_name, fontSize=18,
                                              leading=24, spaceAfter=10,
                                              textColor=HexColor("#1f2937"))
                h2_style = ParagraphStyle("xrz_h2", fontName=font_name, fontSize=14,
                                           leading=20, spaceBefore=10, spaceAfter=4,
                                           textColor=HexColor("#1d4ed8"))
                body_style = ParagraphStyle("xrz_body", fontName=font_name, fontSize=10.5,
                                            leading=16, textColor=HexColor("#374151"))
                bullet_style = ParagraphStyle("xrz_bullet", fontName=font_name, fontSize=10.5,
                                              leading=16, leftIndent=14, bulletIndent=2,
                                              textColor=HexColor("#374151"))

                doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                                        leftMargin=2 * cm, rightMargin=2 * cm,
                                        topMargin=1.8 * cm, bottomMargin=1.8 * cm)

                def _esc(s):
                    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                            .replace(">", "&gt;"))

                story = []
                # 文档标题：内容首个 # 标题，否则用文件名
                text_src = raw_content if isinstance(raw_content, str) else str(raw_content)
                first_h = ""
                for _ln in text_src.splitlines():
                    if _ln.strip().startswith("# "):
                        first_h = _ln.strip()[2:].strip()
                        break
                story.append(Paragraph(_esc(first_h or out_path.stem), title_style))
                story.append(Spacer(1, 6))

                started = first_h != ""  # 首个 # 已作大标题，跳过避免重复
                for raw_line in text_src.splitlines():
                    line = raw_line.rstrip()
                    s = line.strip()
                    if not s:
                        continue
                    if started:
                        started = False
                    if s.startswith("### "):
                        story.append(Paragraph(_esc(s[4:]), h2_style))
                    elif s.startswith("## "):
                        story.append(Paragraph(_esc(s[3:]), h2_style))
                    elif s.startswith("# "):
                        story.append(Paragraph(_esc(s[2:]), title_style))
                    elif s.startswith(("- ", "* ")):
                        story.append(Paragraph("• " + _esc(s[2:]), bullet_style))
                    elif s.startswith("```"):
                        # 代码块行：原样渲染（等宽风格用斜体标记段说明）
                        story.append(Paragraph(_esc(s), bullet_style))
                    else:
                        story.append(Paragraph(_esc(s), body_style))

                doc.build(story)
                _size_kb = round(out_path.stat().st_size / 1024, 1)
                return (f"✅ PDF 已生成: {out_path}（{_size_kb} KB，共 {doc.page} 页）\n"
                        f"   若内容与要求不符，请带上完整内容重新调用。")
            except ImportError as ie:
                raise ImportError("❌ reportlab 未安装。请运行：\"D:/软件/Python/python.exe\" -m pip install reportlab") from ie
            except Exception as e:
                raise Exception(f"❌ PDF 生成失败 ({type(e).__name__}): {e}") from e

        self._tools.register(
            "pdf_create",
            "生成 PDF（reportlab）。content 支持纯文本或 Markdown"
            "（# 大标题 / ## 小节 / - 要点），中文自动用系统 SimSun/SimHei 字体。"
            "只传 path 不传内容会被拒绝。",
            pdf_tool_fn)

        # ─── 记忆工具（母代理直接执行）───
        async def remember_tool(**params):
            if self._memory is None:
                return "错误：记忆管理器未初始化"
            content = params.get("content", "")
            tags = params.get("tags", [])
            mid = self._memory.save(content, tags=tags if isinstance(tags, list) else [tags])
            return f"[记忆已保存] ID={mid}"

        async def recall_tool(**params):
            if self._memory is None:
                return "错误：记忆管理器未初始化"
            query = params.get("query", "")
            results = self._memory.search(query)
            if not results:
                return "[回忆] 未找到相关记忆"
            lines = [f"- [{r.id}] {r.content[:60]}... (相关度: {r.score:.2f})" for r in results]
            return "[回忆结果]\n" + "\n".join(lines)

        async def summarize_tool(**params):
            if self._memory is None:
                return "错误：记忆管理器未初始化"
            period = params.get("period", "today")
            results = self._memory.summarize(period=period)
            return f"[摘要] 共 {len(results)} 条记忆"

        async def list_summaries_tool(**params):
            if self._memory is None:
                return "错误：记忆管理器未初始化"
            limit = params.get("limit", 10)
            return self._memory.list(limit=limit)

        self._tools.register("remember", "保存记忆", remember_tool)
        self._tools.register("recall", "回忆记忆", recall_tool)
        self._tools.register("summarize", "生成摘要", summarize_tool)
        self._tools.register("list_summaries", "列出记忆摘要", list_summaries_tool)

        # ─── 子代理结果查询工具 ───
        async def get_subagent_result_tool(**params):
            if self._subagent_manager is None:
                return "错误：子代理管理器未初始化"
            task_id = params.get("task_id", "")
            task = self._subagent_manager.check_task(task_id)
            if not task:
                return f"任务 {task_id} 不存在"
            if task.status.value != "done":
                return f"任务 {task_id} 状态: {task.status.value}（尚未完成）"
            r = task.result
            parts = [f"[子代理结果] 任务 {task_id}"]
            if r.findings:
                parts.append(f"\n=== 核心发现 ===\n{r.findings[:1500]}")
            if r.output:
                parts.append(f"\n=== 回复 ===\n{r.output[:500]}")
            if r.files:
                parts.append(f"\n=== 文件 ({len(r.files)} 个) ===")
                for fi in r.files[:10]:
                    parts.append(f"  - {fi.get('name', fi.get('path', '?'))}")
            return "\n".join(parts)

        self._tools.register("get_subagent_result", "获取子代理结果", get_subagent_result_tool)

        # ─── Ollama 本地模型对话工具 ───
        async def ollama_chat_tool(**params):
            """通过 Ollama REST API 与本地模型对话"""
            from agent_core.multi_browser import get_multi_browser_manager
            mgr = get_multi_browser_manager()
            if not mgr or not mgr.ollama:
                return "[错误] Ollama 未初始化。请确保 multi_browser 已启动"
            
            prompt = params.get("prompt", "")
            system = params.get("system", "")
            if not prompt:
                return "[错误] 缺少 prompt 参数"
            
            result = await mgr.ollama_chat(prompt, system)
            if result.get("success"):
                return f"[Ollama/{result.get('model', '?')}]\n{result['text']}"
            else:
                return f"[Ollama 错误] {result.get('error', '未知')}"

        self._tools.register("ollama_chat", "通过 Ollama 本地 API 对话（自动检测可用模型）", ollama_chat_tool)

    # ============================================================
    # 系统提示词
    # ============================================================

    def _build_system_prompt(self) -> str:
        """构建系统提示词（包含所有可用工具）"""
        tool_desc_list = []
        for name, info in self._tools._tools.items():
            tool_desc_list.append(f"- {name}: {info['description']}")
        tools_block = "\n".join(tool_desc_list)

        # 多平台 LLM 支持：从平台注册表动态生成（新增网页 AI 只需改 platforms.json）
        try:
            _plats = _list_platforms()
        except Exception:
            _plats = []
        if _plats:
            platforms_block = "\n".join(
                f"- {p['name']}（{p.get('chat_url') or p.get('url')}）" for p in _plats
            )
            platforms_block += "\n- Ollama（本地子进程: ollama run qwen3.5:0.8b，不需要浏览器）"
            platforms_block += "\n\n更多网页 AI 可通过编辑 agent_core/platforms.json 或 "
            platforms_block += "<数据目录>/platforms.user.json 即时接入，无需改动代码。"
        else:
            platforms_block = "- DeepSeek（https://chat.deepseek.com，默认平台）"

        # RAW 命令格式：仅在「本机 shell 真实可用」时宣传。
        # 网页聊天平台（通义/豆包/元宝…）上的模型会照抄这个格式、反复发同一条 shell 命令，
        # 而该命令在本机要么不存在、要么输出乱码 → 模型看不懂就无限重发 → 死循环卡死。
        # 因此对多平台会话【不提供】RAW shell 格式，只允许使用真实工具。
        _is_platform_session = type(self._session).__name__ != "DeepSeekSession"
        if _is_platform_session:
            raw_block = (
                "（本平台【不提供】shell / RAW 命令能力，也禁止输出任何 RAW 命令块"
                "（即用尖括号包住 RAW 三个字母的那种块）；"
                "禁止尝试执行系统命令，一切操作必须通过下面的工具完成。）\n"
                "【严禁使用网页自带功能代替工具】不要使用本网页自带的「办公 / 文档生成 / "
                "PPT 生成 / 表格生成 / 沙箱 / 深度研究 / 上网插件」等任何平台内建功能，"
                "也不要让网页自己去生成文件。这些功能运行在网页自己的沙箱里，"
                "生成的文件【不会】出现在用户电脑上，等于任务失败。\n"
                "生成 Word / PPT / 表格 / 文本等一切文件，"
                "【必须】通过 @@@@ 协议调用 docx_create / pptx_create / file_write 等本 Agent 的工具，"
                "并把工具返回的成功信息作为完成的依据。\n"
                "【特别提醒】一旦看到网页弹出「办公 / 写作 / 演示文稿 / 文档」之类的面板、卡片或"
                "侧边栏，就说明你【走错路了】：那是平台自己的沙箱，在那里做出来的东西用户电脑上一个"
                "文件都不会有，而且会立刻耗尽平台额度、让你后面再也答不上话。"
                "此时必须马上停止，改为直接输出一行 @@@@ 协议调用本 Agent 的工具。\n"
                "同理，【查资料 / 联网检索】也必须调用 browser_search 工具，"
                "不要用网页自带的「联网搜索 / 上网」开关——那样搜到的内容只留在网页里，"
                "本 Agent 和用户都拿不到结果。\n"
                "【身份】当用户问「你是谁 / 你是什么模型」时，必须明确回答："
                "你是「仙人掌 Agent（XianRenZhang Agent）」，当前由本平台的大模型驱动；"
                "不要只报平台的名字，也不要否认自己是仙人掌 Agent。\n"
            )
        else:
            raw_block = (
                "RAW 命令格式（直接执行shell，仅在你确实需要执行系统命令时使用）：\n"
                "<<<RAW>>>\n"
                "echo hello\n"
                "<<<RAW>>>\n"
                "（上面 echo hello 只是格式示例；请把示例换成你真正要执行的命令，"
                "绝不照抄示例本身。）\n"
            )

        return f"""你是仙人掌 Agent（XianRenZhang Agent），一个自主 AI 助手，支持多平台 LLM。

=== 核心指令 ===
当用户提出任务时，你必须根据需要使用工具来完成任务。工具调用格式：

@@@@
{{
    "tool": "工具名称",
    "params": {{...}},
    "id": "唯一标识"
}}
@@@@

{raw_block}
=== 可用工具 ===
{tools_block}

工具使用约定（对标 OpenCode / Codex）：
- 文件操作：read(支持 offset/limit 行范围) / write / edit（mode=replace|append|insert|delete，replace 支持 old/new 或 pattern/repl 正则）/ list / glob(**/*.py) / grep(正则) / apply_patch(统一diff，支持新建/修改/删除)。
- 路径：相对路径基于工作目录；绝对路径按用户要求原样使用（不擅自改写到 C 盘）。
- 网络：webfetch(抓网页正文) / websearch(网络搜索，默认 DuckDuckGo)。
- 任务编排：todowrite/todoread(任务列表与进度) / task(委派子代理并行执行) / session_manager(多会话并行管理)。
- 权限：permission_check/permission_list/permission_set（allow/deny/ask + 通配符，如 mcp_*、git *）。
- 扩展：mcp_client 可对接任意 MCP server（stdio，配置见 DATA_ROOT/mcp.json）。

=== 多平台 LLM 支持 ===
你可以使用以下平台（需要登录或本地运行）：
{platforms_block}

不同平台的特性：
- DeepSeek: 支持文件上传、搜索
- 通义千问: 支持文件上传
- 豆包: 支持文件上传
- 元宝: 支持文件上传
- ChatGPT: 支持文件上传
- Gemini: 支持文件上传
- Ollama: 完全本地运行，通过 `ollama run qwen3.5:0.8b` 子进程通信。AI 调用 ollama_chat 工具时启动 ollama run 并与 qwen3.5:0.8b 对话。

需要让 AI 看图/读文档时，先 `attach_file(paths=[...])` 把图片/PDF 路径加入，
再发对话，文件会自动作为附件上传到输入框。

=== 关键规则 ===
1. **禁止直接在 DeepSeek 界面搜索** - 所有搜索必须使用 `browser_search` 工具
2. 一次只能执行一个工具调用
3. 工具执行后，系统会返回结果，你需要根据结果决定下一步
4. 如需多步骤，请分多次发送工具调用
5. 任务完成后，发送 done() 表示结束
7. **思考过程可视化**：每次调用工具前，先用一两句自然语言表达你的思考或计划，
   并【写在本轮 @@@@ 协议块之外】（纯自然语言，不要放进 JSON）。这部分会被实时抓取，
   作为「🧠 思考过程」展示给用户，让用户看到你的推理链路。
   这条思考过程也必须保留。

8. **文件类任务必须用工具真实完成，绝不许让用户复制粘贴**：你运行在用户的本机电脑上，
   `docx_create` / `pptx_create` / `file_write` / `file_edit` 等工具由本地运行时
   【真实执行】，会在用户电脑上真的生成或修改文件。当用户要求生成 Word / PPT / 文本文件、
   或在某文件末尾追加内容时，你必须调用对应工具并传入完整参数，【绝不要】输出
   「我无法在您的电脑上创建文件，请复制以下内容到 Word」之类的话——那是错误的，
   你其实可以创建。示例：
   - 生成 Word 报告到指定路径：
     @@@@{{"tool":"docx_create","params":{{"content":"# 仙人掌 Agent 自测报告\\n一、功能概览\\n...","path":"C:\\\\Users\\\\X.LAPTOP-CA1GJQE3\\\\Desktop\\\\test\\\\report.docx"}},"id":"1"}}@@@@
   - 在文件末尾追加一行：
     @@@@{{"tool":"file_edit","params":{{"path":"C:\\\\Users\\\\X.LAPTOP-CA1GJQE3\\\\Desktop\\\\test\\\\note.txt","mode":"append","new":"TEST_OK_仙人掌自测"}},"id":"2"}}@@@@
   - 注意工具名必须是上面「可用工具」里列的真名（docx_create / file_edit 等）；
     若你一时想不起准确名，本地运行时会自动把相近的名字映射到正确工具，但请尽量用真名。

=== 任务范围与「绝不偷懒」铁律（极重要）===
- 你【只能】执行用户明确要求的任务，以及完成该任务直接必需的子步骤。
- 【严禁】自行发明新任务；【严禁】主动生成用户没有要求的报告 / 调研 / 文件 / 网页 / 对话。
- 历史对话【仅作上下文参考】，绝不自动续做、绝不重发历史中未完成或未确认的任务。
  即使恢复了历史对话，也【绝不】把历史里任何一条用户消息当作新指令去执行。
- 每次用户发来一条新指令，你只处理那一条指令本身，不要把旧历史混进来自行扩展。
- 【最重要 / 绝不偷懒】你【没有】 ask / question 这类「向用户提问」的工具，也【绝不允许】
  以任何形式把任务踢回给用户、或假装「做不了 / 我无法」。遇到信息不全、路径不确定、
  或拿不准时，自己选择【最合理的默认方案】直接推进，用真实工具把用户的任务做到底，
  然后调用 done() 结束。
- 如果你觉得某件事「用户好像没明确要求」，但它是完成当前这条指令的【直接必需子步骤】
  （例如为了生成报告必须先建目录、为了改文件必须先读取），那就直接做，不要停下来。
- 只有当你想做的东西【完全超出】用户这条指令的范畴（用户没要报告你偏要写报告）时，才不做它；
  但即便如此也【不向用户提问】，只专注于用户真正要求的事，做完后调用 done()。

=== 禁止重复 / 禁止无用 shell（极重要，防卡死）===
- 【严禁】连续两次发送完全相同的工具调用。如果上一次的返回结果说明该操作已经成功，
  就直接进入下一步或调用 done()；如果返回的是错误，就【换一种做法】，绝不要原样重发。
- 【严禁】输出任何 `<<<RAW>>>` / shell 命令块。你所在的网页聊天平台无法在本机执行系统命令，
  就算输出了也只会得到乱码或「不是内部或外部命令」之类的报错。需要读写文件请直接用
  file_write / file_edit / docx_create / pptx_create；需要查资料请用 browser_search / webfetch。
- 工具返回内容与上一次完全一样时，说明这条路走不通，必须改变策略。
- 【严禁】用自然语言宣称「已生成 / 已创建 / 已保存」某个文件。文件是否生成只看工具返回，
  没有成功调用工具就说已生成，属于谎报，会被系统拦截并要求重做。
- 【严禁】把任务交给网页自带的「办公 / 文档 / PPT / 表格 / 沙箱 / 深度研究」等平台内建功能。
  那些功能在网页自己的沙箱里生成文件，用户电脑上不会有任何文件，等于没做。
  一律用 @@@@ 协议调用本 Agent 的 docx_create / pptx_create / file_write 等工具。

=== 子代理规则 ===
- browser_research: 启动独立研究子代理（后台运行，不阻塞母代理）
- browser_visit: 启动独立访问子代理
- 子代理启动后返回 task_id，使用 check_task(task_id) 查询进度
- 子代理不能再创建子代理（MAX_DEPTH=1）

=== 记忆系统 ===
- remember(content, tags): 保存重要信息到长期记忆
- recall(query): 搜索相关记忆
- 每运行约 20 轮会自动提醒保存记忆

=== 文件输出路径 ===
- 产物默认落在工作目录（D 盘项目目录）。
- 【关键】如果用户明确指定了输出路径，你必须**严格按用户指定的路径生成文件**，
  任何盘、任何目录都可以（包括 C 盘），不要擅自改动盘符或目录，也不要重定向到别处。
- 软件自身安装在 D 盘、其内部数据（登录态、对话历史、缓冲、子代理输出、Playwright
  浏览器二进制）由 xrz_paths.py 固定存放在 D 盘项目目录——这跟你「工作时生成产物的
  路径」是两回事，互不影响。你工作时没有盘符限制，用户叫你生成在哪就在哪。

=== 任务结束（由你判断）===
- 任务是否完成、**何时结束，由你自己判断**：工作确实做完了，就调用 done() 结束。
- 不要假称「失败 / 做不了」——如果工具已返回成功，就说明产物已经生成，如实收尾即可。
- 严禁在任务没做完时提前放弃；也没必要在成功后反复「补救」一个已经成功的产物。
- 你自己的上一轮回复【不会被回灌】给你（避免回声干扰）。如需回顾你做过什么，
  调用 recall("...") 从记忆里查询执行进度。

=== 会话历史追溯 ===
- 每次对话会生成唯一的 DeepSeek URL，可通过该 URL 追溯历史
- 使用 save_conversation() 保存当前对话上下文
- 使用 load_conversation(url=...) 加载历史对话

=== 工作目录 ===
所有文件操作默认相对于: {self._work_dir}
（此目录位于 D 盘，是默认落点；用户明确指定的其它路径同样可用，不限制盘符）
"""

    # ============================================================
    # 公共 API
    # ============================================================

    async def start(self, session: Optional[DeepSeekSession] = None):
        """启动 Commander（复用外部 session）"""
        if session:
            self._session = session
        self._running = True
        # 设置系统提示词到 session，这样每轮对话都会包含
        if self._session:
            self._session.set_system_prompt(self._system_prompt)

    def _recent_uploads(self, minutes: int = 15, limit: int = 3) -> list:
        """最近刚上传的附件绝对路径（HTTP /upload 的落盘目录）。

        「上传」和「发指令」常常是两次请求，附件不一定会进 _pending_attachments，
        模型看不到路径就会满磁盘 glob 找文件（实测为此绕了 25 次工具调用）。
        """
        try:
            import time as _t
            base = Path(__file__).resolve().parent.parent / "xrz_data" / "gui_attachments"
            if not base.is_dir():
                return []
            now = _t.time()
            files = [p for p in base.iterdir() if p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return [str(p) for p in files
                    if (now - p.stat().st_mtime) <= minutes * 60][:limit]
        except Exception:
            return []

    async def run(self, user_instruction: str, file_path: Optional[str] = None,
                  context_hints: str = "") -> str:
        """run() 的并发闸门。

        【实测缺陷·必须串行】后端 /command 没有任何并发保护：上一条任务还在跑时
        再发一条指令，两个 run() 会【同时驱动同一个网页会话】（同一浏览器、同一
        PlatformSession / DeepSeekSession）。后果是灾难性的且难以察觉：
          · 两条指令的消息在同一个网页输入框里交替发出 → 模型收到串台上下文；
          · 两个 wait_response 抢同一个页面的「最后一条消息」→ 回复张冠李戴；
          · 会话自愈（换新会话/轮数计数）被两次调用互相触发 → 无限换会话空转；
          · 最终表现为「任务卡死、最终回复丢失（final=''）」。
        这里用 asyncio.Lock 把 run() 串行化：后来的指令排队等前一条跑完再执行。
        """
        try:
            import asyncio as _aio
            _lock = getattr(self, "_run_lock", None)
            if _lock is None:
                _lock = _aio.Lock()
                self._run_lock = _lock
        except Exception:
            _lock = None
        if _lock is None:
            return await self._run_impl(user_instruction, file_path, context_hints)
        if _lock.locked():
            logger.warning("[Commander] 上一条任务仍在执行，新指令已排队（避免同一网页会话被并发驱动）")
            try:
                _warn_evt = getattr(EventType, "WARNING", None) or EventType.ERROR
                self._emit(_warn_evt, {
                    "text": "上一条任务还在执行，你的新指令已排队，稍后自动执行"
                })
            except Exception:
                pass
        # 排队等待有上限：不能因为上一条任务卡住就让后面所有指令永远排队
        # （否则用户会发现「发什么都没反应」，比并发串台还难排查）。
        try:
            await _aio.wait_for(_lock.acquire(), timeout=600)
        except Exception:
            logger.error("[Commander] 等待上一条任务让出执行权超时（600s），放弃本次指令")
            try:
                self._emit(EventType.ERROR, {
                    "text": "上一条任务超过 10 分钟仍未结束，本次指令已取消。"
                            "请先中断上一条任务（或重启软件）再试。"
                })
            except Exception:
                pass
            return "[错误] 上一条任务长时间未结束（很可能卡住了），本次指令已取消。" \
                   "请先中断上一条任务，或重启软件。"
        try:
            return await self._run_impl(user_instruction, file_path, context_hints)
        except BaseException as e:  # noqa: BLE001 - 最后一道防线，见下方说明
            # 【实测事故·必须拦 BaseException】
            # 工具链里冒出的 SystemExit / KeyboardInterrupt 会穿透 Task.__step，
            # 一路终结事件循环线程，导致后端永久返回「事件循环未运行」。
            # 这里把任何逃逸异常都降级成一次普通的失败任务：
            #   · 通知前端（错误事件 + 结束事件），用户能看到失败原因；
            #   · 释放锁，后续指令照常可用；
            #   · 异常不再外泄，事件循环存活。
            if isinstance(e, (KeyboardInterrupt, SystemExit)) and sys.is_finalizing():
                raise
            exc_name = type(e).__name__
            logger.exception("[Commander] 任务执行过程中发生未捕获异常，已降级为失败任务")
            try:
                self._emit(EventType.ERROR, {
                    "text": f"任务中断（{exc_name}）: {e}"
                })
            except Exception:
                pass
            try:
                self._emit(EventType.DONE, {
                    "type": "error",
                    "text": f"任务因内部异常中断（{exc_name}）: {e}",
                })
            except Exception:
                pass
            return f"[错误] 任务中断（{exc_name}）: {e}"
        finally:
            try:
                _lock.release()
            except Exception:
                pass

    async def _run_impl(self, user_instruction: str, file_path: Optional[str] = None,
                        context_hints: str = "") -> str:
        """
        单轮执行（自动循环直到 AI 认为完成）
        返回最终回复内容
        """
        if self._session is None:
            raise RuntimeError("Session 未初始化")

        # 【关键修复】每条命令都强制把系统提示词（工具列表 + @@@@ 协议）注入 session，
        # 否则新建/restore 的会话 self._system_prompt 为空，模型收不到工具说明会坚称
        # 「我无法操作你的电脑」，导致文件类任务全部失败。
        self._session.set_system_prompt(self._system_prompt)

        # 构建第一轮输入（包含系统提示词只在这一轮，后续已存入session）
        current_input = user_instruction
        original_task = user_instruction  # 任务范围守卫：用于拦截 AI 自行发明的新任务
        if file_path:
            current_input += f"\n\n[参考文件: {file_path}]"
        if context_hints:
            current_input += f"\n\n[上下文提示: {context_hints}]"
        # 关键修复：待附件除了上传到 AI 平台网页外，必须把本地绝对路径告诉 AI，
        # 否则 AI 只能瞎猜附件路径（曾猜成 gui_session/sample.pdf 导致找不到文件、
        # 任务假失败）。有了真实路径，AI 可用 file_read 直接读取内容。
        if self._pending_attachments:
            _att_lines = "\n".join(f"- {p}" for p in self._pending_attachments)
            current_input += (
                "\n\n[附件说明] 本任务附带了以下文件，它们已同步上传到平台网页，"
                "同时在本地磁盘保留有原件，请直接用 file_read 读取以下绝对路径，"
                f"不要猜测其它路径：\n{_att_lines}"
            )
        elif any(k in user_instruction for k in ("上传", "附件", "PDF", "pdf", "文档", "这份", "刚才")):
            # 【实测坑】用户在面板里点「上传」是走 HTTP /upload 落盘，
            # 并不一定会进 _pending_attachments（上传和发指令是两次请求）。
            # 结果模型不知道附件在哪，开始满磁盘 glob → 二十多轮工具调用去找文件。
            # 这里把「刚刚上传」的文件路径直接指给它（file_read 支持 PDF）。
            _recent = self._recent_uploads()
            if _recent:
                current_input += (
                    "\n\n[附件说明] 刚刚上传到本机的文件在这里，请用 file_read 直接读取"
                    "（file_read 支持 PDF），不要自己满盘搜索：\n"
                    + "\n".join(f"- {p}" for p in _recent)
                )

        final_reply = ""
        last_ai_text = ""  # 最近一次 AI 的纯文本回复（循环到上限时作为最终答案返回）
        # 「动作-结果」摘要：每轮记录，回灌给模型的是这个而不是 agent 原话
        action_log: list = []
        # 【硬性约束】循环只能通过 AI 显式调用 done() 并经脚本确认后退出。
        # max_turns 设为极大值（≈ 无穷大），绝不因为轮数限制提前踢出 AI。
        # 唯一合法退出路径：AI 发送 {"tool":"done"} → 脚本确认 → break。
        # 安全阀：MAX_NO_PROTOCOL 防止 AI 永远不回协议导致纯纠正死循环（独立于轮数）。
        max_turns, turn = 99999999999, 0
        no_protocol_retries = 0  # 防止通用平台（通义/豆包等）不遵守协议时无限循环
        empty_reply_retries = 0  # 防止「一次没抓到回复」就把整个任务判死
        # 通用平台（豆包等）首轮可能因为页面还在渲染而抓不到正文，允许有限次重发同一请求。
        MAX_EMPTY_REPLY = 3
        # 极大值：通用平台不一定每次都立刻回 @@@@ 协议（尤其首轮还在消化协议时），
        # 绝不能因为几次没回协议就提前 break 退出循环（用户明确要求调成极大值）。
        # 配合「每轮反复发送系统提示词」+「清洗网页 UI 文字」，模型最终会回协议。
        MAX_NO_PROTOCOL = 99999999999
        # 网页聊天平台（通义/豆包/元宝）的模型能力弱，经常整段用自然语言回答、不写 @@@@ 协议。
        # 对它们保留「纠正几次」即可：纠正无效时把那句话当最终回答返回，
        # 绝不能无限重试导致用户侧看起来「卡死」。DeepSeek 仍保持极大值。
        _is_platform_session = type(self._session).__name__ != "DeepSeekSession"
        _max_no_protocol = 2 if _is_platform_session else MAX_NO_PROTOCOL
        # 假完成守卫：弱模型（豆包/元宝）常「口头声称已生成文件」却不真的调工具。
        # 实测：豆包对「生成 mp2_doubao.pptx」只回「已生成 3 页演示文稿…文件已生成」，
        # 而全程 tools=['done']，磁盘上没有该文件 → 必须打回让它真调工具。
        self._file_tools_used = set()
        self._tool_result_log = []   # [(工具名, 结果摘要)] 用于 done() 空文案时生成真实总结
        self._fake_done_retries = 0
        self._empty_done_retries = 0
        self._no_progress_retries = 0      # 无进展循环守卫打回计数（本 run 内）
        self._cmd_sigs = []                # 本 run 内已执行工具调用签名（tool+参数）
        self._no_progress_baseline = None  # 无进展守卫触发时的 _cmd_sigs 长度基线
        MAX_FAKE_DONE = 2
        MAX_EMPTY_DONE = 1
        # 本轮是否为「用户真实输入」（用户插话）→ 决定 internal 标记
        _queued_user_turn = False

        while turn < max_turns and self._running:
            turn += 1
            self._history_turns += 1

            # ── 检查中断标志（每轮开始前）──
            if self._interrupted:
                logger.info(f"[Commander] 第 {turn} 轮前检测到中断，退出循环")
                self._emit(EventType.INTERRUPT, {"text": "用户已中断任务"})
                return "[已中断]"

            # ── 处理消息队列（插话）──
            if self._message_queue:
                queued_msg = self._message_queue.pop(0)
                logger.info(f"[Commander] 处理队列中的插话消息: {queued_msg[:80]}")
                self._emit(EventType.MESSAGE_PROCESSED, {"message": queued_msg})
                # 将插话消息作为新的输入
                current_input = f"[用户插话] {queued_msg}\n\n请暂停当前工作，优先处理用户的插话要求。"
                self._interrupted = False  # 清除中断标志，继续执行
                _queued_user_turn = True   # 用户插话属于真实用户输入，要进历史
                continue

            # 记忆提醒
            if self._memory and self._history_turns % 10 == 0:
                self._emit(EventType.MEMORY_REMINDER, {"turn": self._history_turns})

            # 发送给 AI（携带待附件）
            # internal 标记：本次 run() 的第一轮（真实用户指令）以及用户插话算"用户说的"，
            # 其余所有轮都是 Agent 内部控制轮（工具结果回传 / 协议纠正 / 继续指令）
            # → 不写进对话历史，历史记录里只留用户真实对话。
            try:
                if self._pending_attachments:
                    _att_note = ("\n[系统] 本轮附带以下本地文件（如果网页输入框没有出现附件预览，"
                                 "就用 file_read 工具直接读取这些路径，file_read 支持 PDF）：\n"
                                 + "\n".join(f"- {x}" for x in self._pending_attachments))
                    current_input = current_input + _att_note
                response = await self._session.send(
                    current_input,
                    attachments=self._pending_attachments if self._pending_attachments else None,
                    internal=not (turn == 1 or _queued_user_turn),
                )
                # 【噪音清洗】网页平台会把「Tool xxx does not exists.」这类平台原生
                # 工具报错混进回复正文（实测通义），不做处理会原样出现在最终回复开头。
                try:
                    from agent_core.platform_browser import strip_platform_noise_lines as _spn
                    response = _spn(response)
                except Exception:
                    pass
                self._last_attachment_paths = list(self._pending_attachments or [])
                # 【实测问题】DeepSeek 的 attach 按钮选择器为空 → 网页附件根本没上传，
                # 模型回答「会话没有收到任何 PDF」。附件的本机路径是 100% 存在的，
                # 直接告诉模型用 file_read 读（file_read 支持 PDF），不再依赖网页上传。
                self._pending_attachments = []  # 发送后清空
                _queued_user_turn = False
            except Exception as e:
                logger.error(f"AI 调用失败: {e}")
                try:
                    from .platform_browser import LoginRequiredError as _LRE
                    if isinstance(e, _LRE):
                        return f"[需要登录] {e}"
                except Exception:
                    pass
                try:
                    from .platform_browser import SecurityVerificationRequiredError as _SVRE
                    if isinstance(e, _SVRE):
                        # 反自动化图形验证墙：如实停下、告诉用户去浏览器手动过一次验证，
                        # 绝不自动重发（越刷验证墙越频繁）。
                        return f"[需要手动过验证] {e}"
                except Exception:
                    pass
                return f"[错误] AI 调用失败: {e}"

            # 诊断日志：记录收到的回复长度和是否包含协议标记
            has_protocol = "@@@@" in (response or "")
            logger.info(
                f"[Commander] 第 {turn} 轮收到回复: {len(response or '')} 字符, "
                f"含 @@@@ 协议: {has_protocol}"
            )

            # 解析指令
            cmds = self._protocol.extract_all(response)

            # ── 思考过程可视化 ──
            # 把 AI 在 @@@@ 协议 / <<<RAW>>> 之外的自然语言表达（思考、计划、解释）推送给 GUI，
            # 作为「🧠 思考过程」实时展示。
            # 用户也能看到 Agent 每一步在想什么、打算怎么做。
            _reasoning = self._strip_protocol(response)
            if _reasoning:
                # 记下最近一次模型的自然语言内容，作为死循环/异常收尾时的兜底答复
                last_ai_text = _reasoning
                if not cmds:
                    # AI 这一轮只说了话、没有调工具 → 直接当作思考过程展示
                    self._emit(EventType.THINKING, {"text": _reasoning})
                elif cmds[0].command.tool != "done":
                    # 调工具前的计划/解释 → 展示为思考过程
                    # （done 轮的最终答复会由 ai_final_reply 单独展示，避免重复）
                    self._emit(EventType.THINKING, {"text": _reasoning})

            # 若脚本自动修复了 AI 的 JSON 语法错误，明确记录/告知（透明、便于排查）
            if cmds and getattr(cmds[0], "fixed", False):
                self._emit(EventType.CORRECTION_SENT,
                           {"text": self._protocol.describe_fix(cmds[0])})
            if not cmds:
                ai_text = (response or "").strip()
                # 【彻底废除隐式 done】（用户明令）：
                # 实测元宝第 1 轮回复里明明带着 file_write 的 @@@@ 协议，却因为自然语言里
                # 提到「完成 / done」就被当成收尾直接结束，工具根本没执行。
                # 规则改为：没有显式的 @@@@{"tool":"done"}@@@ 协议就【绝不】结束任务，
                # 一律按「未遵守协议」发警告打回。
                if ai_text and self._implicit_done(ai_text):
                    no_protocol_retries += 1
                    _budget_imp = 20 if self._demanded_output_path(original_task) else max(_max_no_protocol, 4)
                    if no_protocol_retries > _budget_imp:
                        logger.warning("模型始终未显式调用 done()，按最终回复处理（已警告 %d 次）",
                                       no_protocol_retries)
                        _warn_text = "模型一直没用显式 done() 协议收尾，已按最后回复结束任务"
                        _warn_evt = getattr(EventType, "WARNING", None) or getattr(EventType, "ERROR")
                        self._emit(_warn_evt, {"text": _warn_text})
                        final_reply = response
                        break
                    logger.info("[Commander] 检测到自然语言收尾但无显式 done() 协议，警告打回 %d/%d",
                                no_protocol_retries, _budget_imp)
                    self._emit(EventType.CORRECTION_SENT, {
                        "text": "模型想用自然语言收尾，已警告：必须显式调用 done() 协议才算结束"
                    })
                    current_input = (
                        "[SYSTEM] 【禁止用自然语言宣布完成】你的上一条回复没有可执行的 @@@@ 协议"
                        "（或协议格式错误被丢弃）。规则：\n"
                        "1. 要执行操作 → 输出 @@@@{\"tool\":\"工具名\",\"params\":{...},\"id\":\"1\"}@@@@；\n"
                        "2. 任务确实做完 → 必须显式输出 @@@@{\"tool\":\"done\",\"params\":{},\"id\":\"9\"}@@@@ "
                        "才算结束，写成「任务完成」「done()」这类自然语言【无效】。\n"
                        "现在请重新输出正确的协议行。"
                    )
                    continue
                if ai_text and self._looks_like_quota_block(ai_text):
                    # 平台账号额度/次数耗尽（实测豆包页面直接弹：
                    # 「近 7 天办公能力的免费额度用完了，我得休息一阵子了…」）。
                    # 这类提示不是模型回答，重试也没用（网页只会一直弹同一张额度卡），
                    # 必须立刻把真实原因告诉用户并停止，不能假装成模型回答去空转轮次。
                    logger.error(f"[Commander] 平台额度/次数耗尽: {ai_text[:120]!r}")
                    self._emit(EventType.ERROR, {"text": f"该平台账号额度已耗尽：{ai_text[:120]}"})
                    final_reply = (
                        f"[平台额度耗尽] {ai_text.strip()}\n\n"
                        "这不是本软件的问题：该网页账号的免费额度 / 使用次数已用完，"
                        "网页只回额度提示、不再回答。请等额度恢复、开通订阅，或切换到其它平台。"
                    )
                    break
                if ai_text and self._looks_like_platform_error(ai_text):
                    # 平台自身报错 / 网页反馈面板文字 → 不是模型回答，重发同一条请求即可
                    empty_reply_retries += 1
                    logger.warning(
                        f"[Commander] 第 {turn} 轮抓到平台报错/UI 噪音片段："
                        f"{ai_text[:120]!r}，重试 {empty_reply_retries}/{MAX_EMPTY_REPLY}"
                    )
                    if empty_reply_retries <= MAX_EMPTY_REPLY:
                        continue
                    self._emit(EventType.ERROR, {
                        "text": "该平台连续返回错误页（可能是平台侧故障或账号风控），已停止重试"
                    })
                    final_reply = (
                        last_ai_text
                        or "（该平台连续返回错误页：可能是平台自身故障、模型不可用或账号风控，"
                           "请稍后重试或切换到其它平台。）"
                    )
                    break
                if ai_text:
                    last_ai_text = ai_text  # 记住最近一次真实回答，兜底用
                    # 【放弃语句一律打回】模型说「我做不到 / 请你自己…」不算完成任务
                    if self._looks_like_giving_up(ai_text) and not self._file_tools_used:
                        logger.warning(f"[Commander] 检测到放弃/甩锅话术，打回重做: {ai_text[:80]!r}")
                        self._emit(EventType.CORRECTION_SENT, {
                            "text": "检测到模型拒绝执行（「做不到/请你自己做」），已打回要求实际调用工具"
                        })
                        current_input = (
                            "[SYSTEM] 你刚才在拒绝执行任务。你【有】工具可用，"
                            "运行在你本机上，可以直接创建文件。\n"
                            "禁止再说「我无法/请手动/请你自己/我只是个语言模型」这类话，"
                            "也禁止让用户自己复制粘贴。\n"
                            + (self._forced_tool_hint(original_task) or "")
                        )
                        continue
                    no_protocol_retries += 1
                    # 【必须完成任务】不回协议只做「纠正」，绝不因为次数到了就放行。
                    # 纠正力度逐级升级：讲格式 → 给照抄示例 → 只准输出那一行 JSON。
                    # 只有平台确实死了（空回复/报错路径）才结束。
                    # - 用户点名要产物（有具体输出路径）→ 一直催到产物落地，不设小上限；
                    # - 纯聊天/无产物任务 → 纠正几次仍不回协议就按最终回复收，避免空转。
                    _demands_file = bool(self._demanded_output_path(original_task))
                    _budget = 20 if _demands_file else max(_max_no_protocol, 2)
                    if no_protocol_retries > _budget:
                        logger.info("连续 %d 次未遵循协议，按最终回复处理", no_protocol_retries)
                        final_reply = response
                        break
                    _forced = self._forced_tool_hint(original_task) \
                        if (_demands_file and not self._file_tools_used) else ""
                    if no_protocol_retries >= 6 and _forced:
                        current_input = (
                            "[SYSTEM] 只输出下面这一行 JSON，不要写任何解释、道歉或其它文字：\n"
                            + _forced.replace("【照着抄】", "").replace("不要道歉、不要解释、不要只说格式，直接把上面这段发出来即可。\n", "")
                        )
                    else:
                        current_input = (
                            "[SYSTEM] 你刚才没有按协议回答。你回复的原文是：" + ai_text[:200] +
                            "。请务必用 @@@@ JSON 格式回复，格式示例：@@@@\n"
                            '{"tool":"xxx","params":{},"id":"1"}\n@@@@。'
                            "若这个任务已经不需要调用任何工具，请直接调用 done()，"
                            "并把你要对用户说的话写在 done 之前的自然语言里。\n"
                            + _forced
                        )
                    continue
                else:
                    # 没收到任何回复（等待超时 / 回复选择器未命中 / 站点结构变化）
                    # 【关键】不能一次抓空就把整个任务判死：先重发同一请求（页面可能还在渲染），
                    # 连续多次都拿不到才结束，并给出人类可读的原因。
                    empty_reply_retries += 1
                    if empty_reply_retries <= MAX_EMPTY_REPLY:
                        logger.warning(
                            f"[Commander] 第 {turn} 轮未抓到模型回复，重试 "
                            f"{empty_reply_retries}/{MAX_EMPTY_REPLY}"
                        )
                        continue
                    self._emit(EventType.ERROR, {
                        "text": "未收到模型回复（回复选择器未命中、页面未渲染完或站点结构变化）"
                    })
                    final_reply = (
                        last_ai_text
                        or "（未收到模型回复：已连续重试多次仍未从网页抓取到回答正文，"
                           "可能是该平台回复区结构变化或页面加载异常。请稍后重试。）"
                    )
                    break

            # 解析到协议命令，重置纠正计数
            no_protocol_retries = 0

            # 执行第一个指令
            cmd = cmds[0]
            self._emit(EventType.COMMAND_DETECTED, {"tool": cmd.command.tool, "id": cmd.id})
            self._emit(EventType.TOOL_START, {"tool": cmd.command.tool})

            # ── 任务范围守卫：拦截 AI 自行发明的用户未要求的新任务 ──
            guard_msg = self._scope_guard(cmd.command.tool, cmd.command.params, original_task)
            if guard_msg:
                logger.warning(f"[任务范围守卫] 拦截到越界工具调用: {cmd.command.tool} | {guard_msg[:120]}")
                current_input = guard_msg
                continue

            result = await self._execute_command(cmd.command)

            # ── 检查中断标志（工具执行完成后）──
            if self._interrupted:
                logger.info(f"[Commander] 第 {turn} 轮工具执行后检测到中断，退出循环")
                self._emit(EventType.INTERRUPT, {"text": "用户已中断任务"})
                return "[已中断]"

            self._emit(EventType.TOOL_END, {
                "tool": cmd.command.tool,
                "status": result.status,
                "output": (result.output or result.error or "")[:6000],
            })
            self._emit(EventType.COMMAND_SUCCESS if result.status == "success" else EventType.COMMAND_ERROR, result)

            # 记录「动作-结果」摘要：回灌上下文的是这个，绝不回灌 agent 原话
            log_line = f"调用 {result.tool} → {result.status}: {(result.output or result.error)[:240]}"
            action_log.append(log_line)
            # 仅 DeepSeekSession 实现了 set_action_log；多平台会话（PlatformSession）没有，
            # 必须防护，否则切到通义/豆包等平台时会抛 AttributeError 导致整条命令崩溃。
            _sa = getattr(self._session, "set_action_log", None)
            if _sa is not None:
                try:
                    _sa(action_log)
                except Exception:
                    pass
            try:
                if self._memory:
                    self._memory.save(log_line, tags=["agent_action", f"turn_{turn}"])
            except Exception:
                pass

            # ── 死循环检测：连续 5 次完全相同的工具调用（工具名+参数+结果）强制终止 ──
            # 这是 raw_shell 空命令死循环（"极其糟糕"卡死根因）的兜底保护。
            import json as _json
            _tool_name = cmd.command.tool
            try:
                _param_sig = _json.dumps(cmd.command.params, ensure_ascii=False, sort_keys=True)
            except Exception:
                _param_sig = str(cmd.command.params)
            _cmd_sig = f"{_tool_name}|{_param_sig}|{(result.output or result.error or '')[:80]}"
            if _tool_name in self._FILE_TOOLS and str(getattr(result, "status", "")).lower() == "success":
                self._file_tools_used.add(_tool_name)
            try:
                _ro = (getattr(result, "output", None) or getattr(result, "error", None) or "")
                self._tool_result_log.append((_tool_name, str(_ro)[:200]))
            except Exception:
                pass
            if getattr(self, "_prev_cmd_sig", None) == _cmd_sig:
                self._identical_streak = getattr(self, "_identical_streak", 0) + 1
            else:
                self._identical_streak = 1
            self._prev_cmd_sig = _cmd_sig

            # ── 无进展循环守卫（工具轮转）──
            # 上面的 _identical_streak 只防「同一 工具+参数+结果 连续 N 次」；
            # 弱模型会「读→写→读→写」在两种工具间交替空转（参数略有差异时连
            # 相同调用都算不上），streak 每次重置、守卫形同虚设，实测豆包 T3：
            # 用户要 PPT，它在 file_read→docx_create 间空转 18 轮、540s 烧满超时。
            # 规则：最近 10 次调用只在 ≤2 种工具间轮转且没有 done → 第 1 次注入强纠正
            # （点破用户真实需求+该用哪个工具）；纠正后 12 次内仍空转 → 强制停止。
            try:
                if _tool_name != "done":
                    self._cmd_sigs.append(_tool_name)
                    if len(self._cmd_sigs) > 30:
                        del self._cmd_sigs[:len(self._cmd_sigs) - 30]
                    if len(self._cmd_sigs) >= 10 and len(set(self._cmd_sigs[-10:])) <= 2:
                        if getattr(self, "_no_progress_baseline", None) is None:
                            self._no_progress_baseline = len(self._cmd_sigs)
                            _np_kinds = "/".join(sorted(set(self._cmd_sigs[-10:])))
                            logger.warning(
                                f"[Commander] 无进展循环守卫#1: 连续 10 次在 {_np_kinds} 间轮转，注入强纠正")
                            self._emit(EventType.CORRECTION_SENT, {
                                "text": f"检测到无进展循环：连续 10 次在 {_np_kinds} 间轮转，已注入强纠正"})
                            current_input = (
                                f"[SYSTEM] 你连续 10 次只在 {_np_kinds} 两种工具间来回空转，没有任何进展，"
                                "也没有调用匹配用户真实需求的工具。\n"
                                "立即：1) 确认用户到底要什么产物（PPT→pptx_create、Word→docx_create、"
                                "文本→file_write、查资料→browser_search）；"
                                "2) 停止重复调用，直接用正确的工具一次做完；3) 做完调用 done()。\n"
                                f"用户的原始要求是：{(original_task or '')[:200]}"
                            )
                            continue
                        elif len(self._cmd_sigs) - self._no_progress_baseline >= 12:
                            _np_kinds = "/".join(sorted(set(self._cmd_sigs[-10:])))
                            logger.error(
                                f"[Commander] 无进展循环守卫#2: 纠正后仍 12+ 次在 {_np_kinds} 间空转，强制停止")
                            self._emit(EventType.ERROR, {
                                "text": f"Agent 无进展循环（在 {_np_kinds} 间轮转），纠正无效，已自动停止避免失控"})
                            final_reply = (
                                last_ai_text
                                or f"（任务中止：Agent 在 {_np_kinds} 间空转 20+ 次，纠正后仍未调用正确工具/done，"
                                   f"为避免失控已自动停止。请换一种说法再试，或切换到其它平台。）"
                            )
                            break
            except Exception:
                pass

            # done() = AI 自己声明任务完成 → 循环正常结束。
            # 是否完成由 AI 自行判断，脚本不替它决定、也不强制 done()。
            if _tool_name == "done":
                final_reply = self._strip_protocol(response) or last_ai_text or "[完成]"
                # 【实测问题】豆包等弱平台经常 done() 里一个字都不写，用户侧就看到一条
                # 空回复，明明文件已经生成好了却像没干活。这里用真实执行过的工具结果
                # 生成一句实话总结，绝不让它显示空白。
                _from_summary = False
                _fr = (final_reply or "").strip()
                if (not _fr) or _fr in ("[完成]", "完成", "done", "Done", "done()", "[done]"):
                    final_reply = self._summarize_done()
                    _from_summary = True
                # 【实测缺陷·用户看不到答案】网页端弱模型（通义/豆包）常常只回一个
                # 光秃秃的 @@@@{"tool":"done"}@@@@ 协议，一个字的自然语言都不写。
                # 结果用户侧收到的「最终回复」是 _summarize_done() 拼出来的工具原始
                # 输出（例如 6KB 的 Bing 抓取正文），而用户真正要的答案（「最相关的
                # 一个链接标题」）根本没出现。这里打回一次，强制它把答案写出来。
                if _from_summary and self._empty_done_retries < MAX_EMPTY_DONE:
                    self._empty_done_retries += 1
                    logger.warning(
                        "[Commander] done() 未写任何自然语言答复，打回要求补写答案 #%d",
                        self._empty_done_retries)
                    self._emit(EventType.CORRECTION_SENT, {
                        "text": "模型只回了一个空的 done()，已要求它用自然语言写出给用户的答复"
                    })
                    current_input = (
                        "[SYSTEM] 你刚才只输出了 done() 协议，没有写任何给用户的答复文字。"
                        "这样用户看不到你的答案，只会看到工具的原始输出（一堆网页抓取正文），"
                        "这是不合格的。\n"
                        "请重新输出：\n"
                        "1) 先用自然语言把【用户要的答案】写出来（这是给用户看的正文）；\n"
                        "2) 然后再输出 done() 协议收尾。\n"
                        f"用户的原始要求是：{(original_task or '')[:200]}\n"
                        "注意：答案要直接给结论（例如用户问链接标题，就写出那个标题和网址），"
                        "不要复述工具返回的原始抓取内容。"
                    )
                    continue
                _missing = self._claimed_but_missing_files(final_reply, original_task)
                if _missing and self._fake_done_retries < MAX_FAKE_DONE:
                    self._fake_done_retries += 1
                    _gap = ("本任务要求产出文件，但你还没有真正调用工具创建它"
                            if not self._file_tools_used else
                            "工具报告成功，但磁盘上并不存在你声称的文件")
                    _hint = (
                        f"[系统·假完成拦截] 你刚才调用了 done()，但校验不通过：{_gap}。\n"
                        f"缺失的文件：{', '.join(_missing[:3])}\n"
                        f"你上一条回复的原文：{final_reply[:200]}\n"
                        "请【真正调用工具】完成它（file_write / docx_create / pptx_create，"
                        "参数里给出完整绝对路径），并确认工具返回成功。\n"
                        "绝对不要只用自然语言宣称「已生成/已创建」。"
                        "文件确实写好后，再调用 done()。"
                    )
                    self._emit(EventType.CORRECTION_SENT, {
                        "text": f"拦截假完成：声称已生成但文件不存在（{_missing[0]}），已要求实际执行"
                    })
                    logger.warning(f"[Commander] 假完成拦截 #{self._fake_done_retries}: {_missing}")
                    current_input = _hint + "\n" + (self._forced_tool_hint(original_task) or "")
                    continue
                # 【内容假完成拦截】文件确实生成了，但漏写了用户白纸黑字列出的条目
                # （实测豆包 T2：要求追加 ③通风④土壤，它把 docx 整个重写回原来的 2 条，
                #  追加的 2 条凭空蒸发，就喊 done()）。取出最新产物做关键词校验。
                _art = self._latest_artifact_path()
                _content_gap = self._missing_task_items(original_task, _art)
                if _content_gap and self._fake_done_retries < MAX_FAKE_DONE:
                    self._fake_done_retries += 1
                    logger.warning(
                        f"[Commander] 内容假完成拦截 #{self._fake_done_retries}: "
                        f"产物 {_art} 缺少用户列出的条目 {','.join(_content_gap)}")
                    self._emit(EventType.CORRECTION_SENT, {
                        "text": f"拦截内容假完成：产物缺少用户点名的条目（{', '.join(_content_gap)}），已要求补全"})
                    current_input = (
                        f"[系统·内容校验不通过] 你刚才调用 done()，但你写进产物的内容不完整：\n"
                        f"你要求落盘的是这些条目：{', '.join(_content_gap)}，可产物里一个都没有。\n"
                        f"（最新产物：{_art}）\n"
                        "请【真正调用 docx_create / file_write 把用户列出的全部条目都写进同一个文件】"
                        "（追加/合并，不要丢掉已有内容），确认写入成功后再调用 done()。"
                    )
                    continue
                # 【放弃语句一律打回】实测（2026-09-13 DeepSeek PDF 上传问答）：
                # 模型在 done() 的收尾文案里直接写「抱歉，我无法在本次会话中可靠地
                # 完成这一任务……我如实收尾」——任务根本没做就宣告结束。
                # 放弃不算完成：打回，并明确告诉它它有工具（file_read 支持 PDF）。
                if self._looks_like_giving_up(final_reply) and self._fake_done_retries < MAX_FAKE_DONE + 2:
                    self._fake_done_retries += 1
                    _att = getattr(self, "_last_attachment_paths", []) or []
                    _att_hint = ""
                    if _att:
                        _att_hint = ("\n用户刚才上传的文件在这里（用 file_read 读取，支持 PDF）：\n"
                                     + "\n".join(f"- {a}" for a in _att))
                    logger.warning(f"[Commander] done() 收尾是放弃话术，打回 #{self._fake_done_retries}: "
                                   f"{final_reply[:80]!r}")
                    self._emit(EventType.CORRECTION_SENT, {
                        "text": "检测到模型在收尾时放弃任务（「我无法完成/如实收尾」），已打回要求实际执行"
                    })
                    current_input = (
                        "[SYSTEM] 你刚才调用了 done()，但收尾文案是「放弃 / 道歉 / 做不到」。"
                        "这不算完成任务，系统不接受。\n"
                        "你运行在本机上，有真实工具可用：file_read（支持读取 PDF / 文本）、"
                        "file_write / docx_create / pptx_create / browser_search。"
                        "禁止再说「我无法 / 请手动 / 超出能力」，禁止把任务丢回给用户。\n"
                        + _att_hint +
                        "\n请立刻实际调用工具完成任务，做完再调用 done()，"
                        "并在 done() 里写一句真实的结果说明。"
                    )
                    continue
                break

            # 死循环保护：连续相同工具调用且结果完全没变化 → 先向模型下发「换策略」纠正，
            # 纠正无效才优雅收尾（返回模型最后的真实回答，而不是抛 [错误] 让用户看到失败）。
            if self._identical_streak >= 3:
                logger.warning(
                    f"[Commander] 重复工具调用 x{self._identical_streak}: {_tool_name}"
                )
                self._emit(EventType.CORRECTION_SENT,
                           {"text": f"检测到连续 {self._identical_streak} 次相同工具调用 "
                                    f"{_tool_name}，已要求模型更换策略"})
                if self._identical_streak < 6:
                    _hint = (
                        f"[系统] 你已连续 {self._identical_streak} 次调用完全相同的工具 "
                        f"{_tool_name}（参数也一样），且系统返回的结果一模一样。\n"
                        f"上一次的结果是：{(result.output or result.error or '')[:400]}\n"
                    )
                    if _tool_name in ("raw_shell", "shell_exec", "bash"):
                        _hint += (
                            "重复执行同样的 shell 命令不会有任何新结果；本环境的 shell 可能"
                            "并不支持该命令。请【立即停止使用 shell】，改用真实工具："
                            "file_write / file_edit / docx_create / pptx_create / "
                            "browser_search / webfetch。\n"
                        )
                    _hint += (
                        "请立刻改变做法：换一个不同的工具或不同的参数。"
                        "如果任务其实已经完成，请直接调用 done()，并用一两句自然语言"
                        "把结果说给用户听。绝对不要再一次发送同样的调用。"
                    )
                    current_input = _hint
                    continue
                # 纠正 3 次仍无效 → 结束循环，但把模型已有的真实内容作为最终回复返回
                logger.error(
                    f"[Commander] 死循环：连续 {self._identical_streak} 次相同工具调用 "
                    f"{_tool_name}，纠正无效，已停止重复操作"
                )
                self._emit(EventType.ERROR, {
                    "text": f"检测到 Agent 重复执行同一个操作 {self._identical_streak} 次，"
                            f"已自动停止以免卡死"
                })
                final_reply = (
                    last_ai_text
                    or self._strip_protocol(response)
                    or f"（任务已中止：Agent 连续 {self._identical_streak} 次重复同一个操作 "
                       f"{_tool_name}，为避免卡死已自动停止。请换一种说法再试一次。）"
                )
                break

            # 构建下一轮输入：只回传工具的真实执行结果（成功/失败都如实），
            # 让 AI 自己判断是否已做完、是否该调用 done()。脚本不强制。
            current_input = (
                f"[系统] 工具 {result.tool} 执行结果:\n{result.output or result.error}\n\n"
                f"请继续；任务确实已完成时，调用 done() 结束。"
                f"如果你刚才执行了相同的工具调用且结果没变化，请换方法或直接 done()。"
            )

        return final_reply or last_ai_text or "[完成]"

    async def run_with_loop(self, user_instruction: str, file_path: Optional[str] = None,
                            context_hints: str = "") -> str:
        """带自动循环的 run（和 run 相同，但名称更清晰）"""
        return await self.run(user_instruction, file_path, context_hints)

    async def continue_dialog(self) -> str:
        """
        继续对话（用户发送 '继续' 时调用）
        返回 AI 的回复内容
        """
        if self._correction_pending:
            correction = self._correction_pending
            self._correction_pending = None
            return await self.run(f"[系统纠正]\n{correction}")

        # 发送继续指令
        return await self.run("[系统] 用户要求继续，请接着上一步执行。")

    async def remember(self, content: str, tags: Optional[list] = None):
        """显式保存记忆"""
        if self._memory:
            mid = self._memory.save(content, tags=tags or [])
            return mid
        return None

    async def recall(self, query: str) -> list:
        """显式回忆记忆"""
        if self._memory:
            return self._memory.search(query)
        return []

    def set_memory_manager(self, mm: MemoryManager):
        """设置记忆管理器（外部注入）"""
        self._memory = mm

    def set_subagent_manager(self, sm):
        """设置子代理管理器（外部注入）"""
        self._subagent_manager = sm

    def stop(self):
        """停止运行循环"""
        self._running = False
        self._interrupted = True

    def interrupt(self):
        """用户中断（暂停），设置中断标志"""
        self._interrupted = True
        self._emit(EventType.INTERRUPT, {"text": "用户已中断，等待后续指令"})

    def queue_message(self, message: str):
        """将插话消息加入队列"""
        self._message_queue.append(message)
        self._emit(EventType.MESSAGE_QUEUED, {"message": message, "queue_size": len(self._message_queue)})

    def get_next_message(self) -> Optional[str]:
        """从队列取出下一条消息"""
        if self._message_queue:
            return self._message_queue.pop(0)
        return None

    def inject_correction(self, correction_text: str):
        """注入系统纠正（AI 幻觉时人工干预）"""
        self._correction_pending = correction_text

    # ============================================================
    # 内部方法
    # ============================================================

    # 任务范围守卫：拦截「用户未明确要求的产物/调研/文件」类工具调用
    _DELIVERABLE_TOOLS = {
        "docx_create", "pptx_create",
        "browser_research", "browser_visit",
        "browser_search", "web_search", "summarize",
    }

    def _scope_guard(self, tool: str, params: dict, original_task: str) -> str:
        """若 AI 试图执行一个与用户原始指令无关的新任务（报告/调研/文件等），
        返回一段强制确认的系统指令；否则返回 None（放行）。"""
        if tool not in self._DELIVERABLE_TOOLS:
            return None
        arg = " ".join(str(v) for v in (params or {}).values() if isinstance(v, str))
        if not arg.strip() or not (original_task or "").strip():
            return None

        def _grams(s: str) -> set:
            s = re.sub(r"\s+", "", s or "")
            return set(s[i:i + 2] for i in range(len(s) - 1))

        og, ag = _grams(original_task), _grams(arg)
        if og and ag and (og & ag):
            return None  # 与原始任务有重叠，视为同一范畴，放行
        # 明显无关 → 禁止执行（但不许把任务踢回给用户，直接聚焦用户真正的要求）
        summary = arg[:80].replace("\n", " ")
        return (
            f"[SYSTEM] 你正准备执行一个用户【未明确要求】的任务（工具 {tool}，"
            f"参数摘要：「{summary}」），这与用户原始指令「{(original_task or '')[:120]}」无关。"
            f"禁止自行执行，也【不要向用户提问】。请忽略这件事，只专注于完成用户真正要求的任务；"
            f"你已把用户的任务做完时，调用 done() 结束即可。"
        )

    async def _execute_command(self, cmd: SimpleNamespace) -> ExecutionResult:
        """执行单个命令"""
        tool_name = getattr(cmd, "tool", None) or getattr(cmd, "action", None)
        params = getattr(cmd, "params", {}) or {}
        cmd_id = getattr(cmd, "id", str(uuid.uuid4()))

        if not tool_name:
            return ExecutionResult(id=cmd_id, status="error", error="指令缺少 tool 字段")

        # 特殊指令处理
        if tool_name == "done":
            return ExecutionResult(id=cmd_id, status="success", output="任务完成", tool="done")

        # ask / question 已被禁用：agent 没有「向用户提问」的权限，禁止偷懒把任务踢回给用户。
        # 若模型仍发出这类调用，明确拒绝并强制它用真实工具把任务推进到底。
        if tool_name in ("ask", "question"):
            return ExecutionResult(
                id=cmd_id, status="success",
                output=(
                    "[系统] 你【没有】 ask / question 工具，也不允许向用户提问或把任务踢回给用户。"
                    "遇到不确定或信息不全时，自己选择最合理的默认方案，直接用真实工具把用户的任务"
                    "做到底，完成后再调用 done()。现在请立即改用正确的工具继续推进。"
                ),
                tool=tool_name)

        # RAW 命令处理
        if tool_name == "raw_shell":
            return await self._tools.execute("shell_exec", {"command": params.get("command", "")})

        # 标准工具调用
        return await self._tools.execute(tool_name, params)

    def _summarize_done(self) -> str:
        """done() 没写文案时，用真实执行过的工具结果生成一句实话总结。

        实测豆包 file_write 成功建了文件，却回了个空的 done()，用户侧看到的就是
        一条空白回复 —— 明明干完活了却像没干。这里如实汇报做了什么。
        """
        if not getattr(self, "_tool_result_log", None):
            return "任务已完成。"
        lines = []
        for name, out in self._tool_result_log[-6:]:
            o = " ".join(str(out).split())
            if not o:
                o = "已执行"
            lines.append(f"- {name}：{o[:160]}")
        body = "\n".join(lines)
        return "已完成。执行记录：\n" + body

    @staticmethod
    def _looks_like_platform_error(text: str) -> bool:
        """判断抓到的这段文本是不是「平台自身报错 / 网页 UI 噪音」，而不是模型回答。

        实测案例（通义千问 Qwen Studio）：
          - 「Oops! There was an issue connecting to Qwen3.8-Max.」
          - 「你更喜欢哪个回复？请选择一个以继续。回复 1 … 我更喜欢这个回复」
        这类内容既不是模型回答、又不是协议指令，如果按「没遵守协议」处理就会白白
        空转好几轮；正确做法是判定为平台瞬时报错，直接重发同一条请求。
        """
        t = (text or "").strip()
        if not t:
            return False
        strong = (
            "oops! there was an issue connecting",
            "an unexpected error occurred",
            "你更喜欢哪个回复",
            "我更喜欢这个回复",
            "请选择一个以继续",
            "此反馈将帮助我们评估",
            # 元宝 A/B 反馈面板（实测会把真实回答顶掉）
            "您正在提供关于", "您更喜欢哪个回答", "我更喜欢这个回答",
            "the model is currently unavailable",
            "系统繁忙",
            "服务器繁忙",
            "请求过于频繁",
            "服务异常",
            "网络异常",
            "生成失败，请重试",
            "内容生成失败",
            # 会话静默失效时平台层可能回传的占位文案
            "（未收到回复）",
            "(未收到回复)",
            "未收到回复",
        )
        low = t.lower()
        return any(s.lower() in low for s in strong)

    @staticmethod
    def _looks_like_quota_block(text: str) -> bool:
        """判断抓到的是不是「平台账号额度 / 使用次数耗尽」提示卡。

        实测（豆包 2026-09-13）：页面底部固定弹一张卡
        「近 7 天办公能力的免费额度用完了，我得休息一阵子了，预计 9 月 20 日 00:48
        恢复为你服务…开通豆包订阅，免等待，继续为你服务。」
        我们把这张卡当成模型回复读回来了（79 字），于是后面每一轮都拿到同一段提示，
        任务被判成「模型不回协议」，白烧好几轮还说不清原因。
        """
        t = (text or "").strip()
        if not t or len(t) > 300:
            return False
        toks = (
            "免费额度用完", "免费额度已用完", "额度用完", "额度已用完", "额度用完了",
            "免费额度不足", "额度不足", "次数已用完", "使用次数已达上限",
            "今日额度已用完", "达到上限", "已达上限",
            "恢复为你服务", "开通豆包订阅", "免等待，继续为你服务",
            "休息一阵子", "稍后再来", "明天再来",
            # 【实测 2026-09-13 通义】账号打到每日上限时页面回的是：
            # 「Oops! There was an issue connecting to Qwen3.8-Max.\n
            #   你已达到每日使用限制。请在 15 小时后再试。」
            # 旧关键词表里没有这句 → 被当成「平台报错」重试 3 次（每轮 40s+ 白等），
            # 最后才抛出「未收到模型回复」，用户白白等几分钟还不知道为什么。
            "你已达到每日使用限制", "达到每日使用限制", "每日使用限制",
            "使用限制", "使用次数已达上限", "今日使用次数已达上限",
            "请在 15 小时后再试", "小时后再试",
        )
        return any(k in t for k in toks)

    @staticmethod
    def _implicit_done(text: str) -> bool:
        """识别「自然语言形式的 done()」。

        实测：豆包等网页聊天平台上的模型经常不写 @@@@ 协议，而是直接回
        「……我是仙人掌 Agent…… done ()」。把它当成协议违规去纠正只会无限空转，
        正确做法是识别出这个完成信号，直接把它的自然语言答复作为最终回复返回。
        """
        return bool(re.search(r"done\s*\(\s*\)", text or "", re.I))

    @staticmethod
    def _emit_implicit_done_clean(text: str) -> str:
        """去掉自然语言里的 done() 痕迹，留下要展示给用户的正文。"""
        return re.sub(r"done\s*\(\s*\)", "", text or "", flags=re.I).strip()

    def _emit(self, event_type: EventType, data=None):
        """触发事件回调"""
        if self._on_event:
            try:
                self._on_event(AgentEvent(event_type=event_type, data=data))
            except Exception as e:
                logger.warning(f"事件回调错误: {e}")

    @staticmethod
    def _strip_protocol(text: str) -> str:
        """去掉 @@@@ / <<<RAW>>> 协议块，保留自然语言部分作为最终回复。"""
        import re as _re
        t = _re.sub(r"@@@@.*?@@@@", "", text or "", flags=_re.DOTALL)
        t = _re.sub(r"<<<RAW>>>.*?<<<RAW>>>", "", t, flags=_re.DOTALL)
        return t.strip()

    # 中文/ASCII 混排路径被网页渲染或弱模型插空格的修复。
    # 实测案例（豆包）：模型给出 "D:\ 软件 \XianRenZhangAgent\..."，
    # 中文字符两侧被插入空格 → 工具报 [WinError 3] 系统找不到指定的路径。
    _CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef"

    @classmethod
    def _path_candidates(cls, raw: str) -> list:
        """给出「原样 → 逐级修复」的候选路径，原样永远排第一（不破坏正常路径）。"""
        import re as _re
        s = str(raw or "")
        if not s.strip():
            return [s]
        cands = [s]
        t = s.strip()
        t = t.strip("`").strip()
        # 去掉首尾成对的引号（模型常把路径连引号一起写进 JSON 值）
        for q in ('"', "'", "“", "”", "‘", "’"):
            if t.startswith(q) and t.endswith(q) and len(t) > 1:
                t = t[1:-1].strip()
        if t != s:
            cands.append(t)
        # 中文字符相邻的空白是插入物：删掉（合法路径里中文两侧也不会带空格）
        t2 = _re.sub(r"[ \t]+(?=[" + cls._CJK + r"])", "", t)
        t2 = _re.sub(r"(?<=[" + cls._CJK + r"])[ \t]+", "", t2)
        # 路径分隔符紧跟的空白同样是插入物（'\ mp2.docx'），
        # 但「Program Files」这种段内空格必须保留 → 只删分隔符后面的空白
        t2 = _re.sub(r"(?<=[\\/])[ \t]+", "", t2)
        if t2 != t:
            cands.append(t2)
        # 最后兜底：干掉所有空白与换行（路径被折行读成空格的情形）
        t3 = _re.sub(r"\s+", "", t2)
        if t3 != t2:
            cands.append(t3)
        # 去重保序
        out = []
        for c in cands:
            if c and c not in out:
                out.append(c)
        return out

    def _safe_path(self, p) -> "Path":
        """解析用户指定的文件/目录路径：
        - 相对路径 → 拼到工作目录（D 盘）
        - **绝对路径：原样尊重用户指定**（用户叫在哪生成就在哪生成，
          包括 C 盘或其它盘，脚本绝不擅自重定向/改写路径）
        - 路径被网页渲染/弱模型插空格损坏时自动修复（见 _path_candidates）
        注意：agent 自身内部数据（profile/cookies/历史/任务/缓冲/子代理输出/
        Playwright 二进制）由 agent_core/xrz_paths.py 固定落在 D 盘，
        与「用户要求的产物输出路径」是两回事，互不影响。
        """
        from pathlib import Path as _P

        def _mk(cand):
            q = _P(cand)
            return q if q.is_absolute() else _P(self._work_dir) / q

        first = _mk(str(p or ""))
        # 原始路径自身就以空白开头的文件名（'\ mp2.docx'）几乎一定是被插入空格，
        # 这种情况不能凭「父目录存在」就原样采用，必须走修复候选。
        _suspicious = bool(first.name) and (first.name != first.name.lstrip())
        # 原样路径的父目录已存在（或它本身就是目录/盘符）→ 直接采用，绝不改写
        try:
            if (first.exists() or (first.parent.exists() and not _suspicious)):
                return first
        except (OSError, ValueError):
            pass
        for cand in self._path_candidates(p):
            try:
                q = _mk(cand)
                q_suspicious = bool(q.name) and (q.name != q.name.lstrip())
                if q.exists() or (q.parent.exists() and not q_suspicious):
                    if q != first:
                        logger.info(f"[路径修复] {p!r} → {q}")
                    return q
            except (OSError, ValueError):
                continue
        return first

    # ── 假完成 / 未执行守卫 ──
    _FILE_TOOLS = {"file_write", "file_edit", "docx_create", "pptx_create",
                   "xlsx_create", "apply_patch", "dir_create", "file_copy", "file_move"}
    _ART_EXT = (".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".pdf",
                ".txt", ".md", ".csv", ".json", ".py", ".js", ".html")

    @staticmethod
    def _demanded_output_path(task_text: str) -> str:
        """从任务原文里取出用户点名要求的产物路径（如 "path 设为 D:\\...\\a.pptx"）。"""
        import re as _re
        for raw in _re.findall(r"[A-Za-z]:[\\/][^\s\"'<>|?*]*?\.[A-Za-z0-9]{1,6}",
                               str(task_text or "")):
            if raw.lower().endswith(Commander._ART_EXT):
                return raw
        return ""

    # 「放弃/拒绝/甩锅给用户」话术 —— 一律打回重做，绝不允许靠一句
    # 「我无法…请你自己做」就结束任务（用户原话：检测到放弃语句自动打回）。
    _GIVEUP_TOKENS = (
        "我放弃", "放弃这个", "无法完成", "不能完成", "无法执行该", "我做不到", "做不到",
        "我无法", "我不能", "我没办法", "我没有办法", "无法做到", "无法实现",
        "请手动", "请你自己", "建议您自己", "请自行", "由你手动", "自己去完成",
        "请复制粘贴到", "您可以复制到", "请粘贴到",
        "我只是一个", "我只是个", "我并没有", "我没有能力", "超出我的能力",
        "我无法访问你的", "我无法操作你的", "无法操作本地", "无法访问本地",
        "I cannot", "I can't", "I am unable", "I'm unable", "unable to",
    )

    @classmethod
    def _looks_like_giving_up(cls, text: str) -> bool:
        """模型在甩锅/认输吗？是就打回重做。"""
        t = str(text or "")
        if not t:
            return False
        low = t.lower()
        for k in cls._GIVEUP_TOKENS:
            if k.isascii() and k.isalpha():
                if k.lower() in low:
                    return True
            elif k in t:
                return True
        return False

    def _forced_tool_hint(self, task_text: str) -> str:
        """给弱模型一段「照着抄就行」的显式工具调用示例。

        实测通义/豆包在连续几轮协议纠正后会陷入「道歉但不执行」，
        空谈格式却不真的调用工具。此时把用户点名的路径直接写进示例 JSON，
        模型照抄即可落地，比抽象地讲规则有效得多。
        """
        path = self._demanded_output_path(task_text)
        if not path:
            return ""
        low = path.lower()
        if low.endswith((".docx", ".doc")):
            tool = "docx_create"
            params = ('{"path":"%s","content":"# 标题\\n正文内容"}' % path.replace("\\", "\\\\"))
        elif low.endswith((".pptx", ".ppt")):
            tool = "pptx_create"
            params = ('{"path":"%s","content":"# 第1页 综合测试\\n---\\n# 第2页 功能展示\\n---\\n# 第3页 谢谢"}'
                      % path.replace("\\", "\\\\"))
        elif low.endswith((".xlsx", ".xls", ".csv")):
            tool = "xlsx_create"
            params = '{"path":"%s","content":"列1,列2\\n值1,值2"}' % path.replace("\\", "\\\\")
        elif low.endswith(".pdf"):
            tool = "pdf_create"
            params = ('{"path":"%s","content":"# 标题\\n正文内容"}' % path.replace("\\", "\\\\"))
        else:
            tool = "file_write"
            params = '{"path":"%s","content":"文件内容"}' % path.replace("\\", "\\\\")
        return (
            "【照着抄】把下面的 JSON 原样输出（路径已经是用户要求的，不要再改动）：\n"
            "@@@@\n"
            '{"tool":"%s","params":%s,"id":"1"}\n'
            "@@@@\n"
            "不要道歉、不要解释、不要只说格式，直接把上面这段发出来即可。\n"
            % (tool, params)
        )

    def _claimed_but_missing_files(self, reply: str, task_text: str = "") -> list:
        """判断 done() 是否是「假完成」。返回声称已生成但磁盘上不存在的文件列表。

        触发条件（任一）：
          1) 回复里给出了具体产物路径，但磁盘上没有该文件；
          2) 回复声称「已生成/已创建/已保存」某产物，而本次任务一次文件工具都没成功调用；
          3) 【关键】任务原文明确要求把产物写到某个带后缀的路径（如
             "path 设为 D:\\...\\mp2_doubao.pptx"），但本次任务没有任何文件工具成功调用
             —— 实测豆包会把这类请求丢给网页自带的「豆包办公」沙箱自己生成，
             全程只调用 done()，本机根本没有文件。
        纯聊天任务绝不触发，避免误伤。
        """
        import re as _re
        from pathlib import Path as _P
        text = str(reply or "")
        task = str(task_text or "")
        paths = _re.findall(r"[A-Za-z]:\\[^\s\"'<>|?*]*?\.[A-Za-z0-9]{1,6}", text)
        paths += _re.findall(r"[A-Za-z]:/[^\s\"'<>|?*]*?\.[A-Za-z0-9]{1,6}", text)
        missing = []
        for raw in paths:
            if not raw.lower().endswith(self._ART_EXT):
                continue
            try:
                q = _P(raw.strip().strip("`\"'"))
                if not q.exists():
                    missing.append(str(q))
            except (OSError, ValueError):
                continue
        if missing:
            return missing
        # 任务原文点名要求产出某个文件路径 → 本次必须有文件工具成功执行过
        if not self._file_tools_used and task:
            tp = _re.findall(r"[A-Za-z]:[\\/][^\s\"'<>|?*]*?\.[A-Za-z0-9]{1,6}", task)
            for raw in tp:
                if not raw.lower().endswith(self._ART_EXT):
                    continue
                try:
                    cand = self._safe_path(raw.strip().strip("`\"'"))
                    if not cand.exists():
                        return [str(cand)]
                except (OSError, ValueError):
                    continue
        # 回复声称已生成产物，但整个任务一次文件工具都没调用过 → 也是假完成
        claim = any(k in text for k in ("已生成", "已创建", "已保存", "生成完毕",
                                        "文件已", "已写好", "已输出"))
        asks_file = any(ext in text for ext in self._ART_EXT)
        if claim and asks_file and not self._file_tools_used:
            _m = _re.search(r"[\w\-.]+\.(?:docx|pptx|xlsx|pdf|txt|md|csv|json|py|js|html)",
                            text, _re.I)
            return [_m.group(0) if _m else "(未给出具体路径的产物)"]
        return []

    def _latest_artifact_path(self) -> str:
        """本 run 内最近一次【真实写入且文件存在】的产物绝对路径（无则空）。

        从 _tool_result_log（[(工具名, 结果摘要)]）里逆序找：取一个文件工具
        （file_write/docx_create/pptx_create/...）成功输出里出现的、且磁盘上
        确实存在的产物路径。用于 done() 时做「内容校验」——弱模型常声称写好了，
        实际只写了一半（实测豆包：要求追加 ③通风④土壤，却把整个 docx 重写回
        原来的 2 条，追加的 2 条凭空蒸发，就喊 done()）。
        """
        import re as _re
        from pathlib import Path as _P
        for _tool, _ro in reversed(getattr(self, "_tool_result_log", []) or []):
            if _tool not in self._FILE_TOOLS:
                continue
            for m in _re.finditer(r"([A-Za-z]:[\\/][^\s\"'<>|?*]*?\.[A-Za-z0-9]{1,6})", _ro or ""):
                raw = m.group(1)
                if raw.lower().endswith(self._ART_EXT):
                    try:
                        p = _P(raw)
                        if p.exists():
                            return str(p)
                    except (OSError, ValueError):
                        continue
        return ""

    def _missing_task_items(self, task_text: str, artifact_path: str) -> list:
        """任务里用中文圆圈编号（①②③④…）明确列出的条目关键词，在产物里缺失的列表。

        仅当任务确实用了「③通风」「④土壤」这类编号条目时才生效——用户白纸黑字
        列出来的条目应当全部落进产物。若最新产物里这些关键词一个都没有，说明弱
        模型没真按用户列的要点写（常是重写时把要求的条目弄丢），应打回。
        纯聊天 / 无编号条目的任务返回空，绝不误伤。
        """
        import re as _re
        from pathlib import Path as _P
        task = str(task_text or "")
        if not task or not artifact_path:
            return []
        # 提取 ①..⑩ 编号后紧跟的 2-8 个汉字关键词
        items = _re.findall(r"([①②③④⑤⑥⑦⑧⑨⑩])([\u4e00-\u9fff]{2,8})", task)
        if not items:
            return []
        try:
            p = _P(artifact_path)
            if not p.exists():
                return []
            suf = p.suffix.lower()
            if suf == ".docx":
                import docx as _docx
                art_text = "\n".join(par.text for par in _docx.Document(str(p)).paragraphs)
            elif suf in (".txt", ".md", ".csv", ".json", ".py", ".html", ".js"):
                art_text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                return []   # pptx/xlsx/pdf 等暂不做关键词校验（避免误伤）
        except Exception:
            return []
        return [kw for _, kw in items if kw not in art_text]


    # ============================================================
    # 错误纠正（commander fix）
    # ============================================================

    async def fix(self, error_hint: str):
        """
        当检测到 AI 幻觉/错误时，注入系统级纠正
        使用方式：在 terminal.py 检测到异常后调用 commander.fix("纠正内容")
        """
        self.inject_correction(error_hint)
        return await self.continue_dialog()


# ============================================================
# 快捷函数（兼容旧代码）
# ============================================================

async def run_agent(browser_manager, session: DeepSeekSession, instruction: str, work_dir: str = "") -> str:
    """快捷函数：创建 Commander 并执行"""
    commander = Commander(
        browser_manager=browser_manager,
        session=session,
        work_dir=work_dir or os.getcwd(),
    )
    await commander.start(session=session)
    return await commander.run(instruction)
