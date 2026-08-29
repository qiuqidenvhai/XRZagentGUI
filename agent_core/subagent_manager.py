"""
subagent_manager.py - 子代理管理器

核心改进：
- 所有子代理复用 NEW_BROWSER_DATA_ROOT/deepseek（browser_profiles/deepseek）
- 清理锁文件后即可启动，共享同一套 cookies
- 子代理之间不会冲突登录态
"""
import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

# 全局单例
_global_manager: Optional["SubAgentManager"] = None

# 子代理嵌套深度（防止子代理再派生子代理，即 MAX_DEPTH=1）
# 旧实现只靠 LLM 提示约束；进程内化后一旦失控会卡死母代理，故在代码层硬限。
_SUBAGENT_DEPTH = 0

# 浏览器数据目录（全部落在 D 盘项目目录内，绝不写 C:\Users\...）
from agent_core.xrz_paths import (
    NEW_BROWSER_DATA_ROOT,
    SUBAGENT_USER_DATA_DIR,
    SUBAGENT_COOKIE_FILE,
    OLD_USER_DATA_DIR,
    OLD_COOKIE_FILE,
    SUBAGENT_TASKS_DIR,
)

def get_subagent_manager(work_dir: str = None) -> "SubAgentManager":
    global _global_manager
    if _global_manager is None:
        _global_manager = SubAgentManager(work_dir)
    return _global_manager


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return self.value


@dataclass
class SubAgentResult:
    success: bool
    findings: str = ""
    output: str = ""
    files: List[Dict] = field(default_factory=list)
    scraped_count: int = 0
    error: Optional[str] = None


@dataclass
class SubAgentTask:
    task_id: str
    task_type: str = "browse"           # "browse" | "research" | "custom"
    query: str = ""
    status: TaskStatus = TaskStatus.PENDING
    result_path: str = ""
    result: Optional[SubAgentResult] = None
    started_at: float = 0.0
    finished_at: float = 0.0
    error: Optional[str] = None


@dataclass
class CompletionNotification:
    """子代理任务完成通知"""
    task_id: str
    task_type: str
    query: str
    success: bool
    result_path: str
    findings_preview: str  # 前200字摘要
    scraped_count: int
    error: Optional[str] = None
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.finished_at))
        return d


class SubAgentManager:
    """
    子代理管理器 - 真实子代理架构
    - 母代理用同一浏览器实例执行子代理任务
    - 主凭据目录永不移动，子代理每次复制到临时目录使用
    - 任务完成写入通知队列，母代理主动提示用户
    """

    def __init__(self, work_dir: str = None):
        self.work_dir = Path(work_dir) if work_dir else SUBAGENT_TASKS_DIR
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # 任务表
        self._tasks: Dict[str, SubAgentTask] = {}
        self._notification_queue: List[CompletionNotification] = []
        # 完成通知回调（母代理可注册）
        self._notify_callback: Optional[Callable[[CompletionNotification], None]] = None
        self._closed = False
        # 母代理的浏览器管理器（用于在同一浏览器内开子窗口，共享登录态）
        self._mother_bm = None

    def set_browser_manager(self, bm):
        """注入母代理的 BrowserManager，子代理将复用同一浏览器开新窗口。"""
        self._mother_bm = bm

    # ─────────────────────────────────────────────────────────
    # 凭据管理 API（给母代理/子代理共用）
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def get_credentials_dir() -> Path:
        """返回用户数据目录（所有子代理共用新版路径）"""
        # 兼容旧路径
        return SUBAGENT_USER_DATA_DIR

    @staticmethod
    def get_cookie_file() -> Path:
        """返回 cookies 文件路径（支持旧路径兼容）"""
        if SUBAGENT_COOKIE_FILE.exists():
            return SUBAGENT_COOKIE_FILE
        if OLD_COOKIE_FILE.exists():
            return OLD_COOKIE_FILE
        return SUBAGENT_COOKIE_FILE

    @staticmethod
    def is_logged_in() -> bool:
        """检测是否已登录（cookie存在且未过期）"""
        cookie_file = SubAgentManager.get_cookie_file()
        if not cookie_file.exists():
            return False
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            if not cookies:
                return False
            important = [c for c in cookies if any(k in c.get("name", "") for k in ["session", "token", "uid", "sid"])]
            if not important and len(cookies) < 3:
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def cleanup_lock_files(data_dir: Path = None) -> bool:
        """清理 Chromium 锁文件，防止重复启动冲突"""
        if data_dir is None:
            data_dir = SUBAGENT_USER_DATA_DIR
        lock_files = ["SingletonLock", "SingletonCookieLock", "SingletonSocketLock", 
                       "SingletonPipeline", "Chrome_Port", "chrome_debug_port", 
                       "SingletonCookie", "lock.file", "SingletonVar"]
        cleaned = False
        # 同时清理旧目录
        dirs_to_clean = [data_dir]
        if data_dir != OLD_USER_DATA_DIR and OLD_USER_DATA_DIR.exists():
            dirs_to_clean.append(OLD_USER_DATA_DIR)
        
        for d in dirs_to_clean:
            if not d.exists():
                continue
            for p in d.iterdir():
                if any(x in p.name.lower() for x in lock_files):
                    try:
                        p.unlink(missing_ok=True)
                        cleaned = True
                    except Exception:
                        pass
        return cleaned

    # ─────────────────────────────────────────────────────────
    # 任务派发（由母代理调用，子代理执行）
    # ─────────────────────────────────────────────────────────

    async def dispatch_custom(self, task_name: str, params: dict, task_id: str = None) -> str:
        """派发自定义任务（未来：基于skill执行）"""
        if task_id is None:
            task_id = f"custom_{int(time.time()*1000)}"
        result_path = str(self.work_dir / f"result_{task_id}.json")
        task = SubAgentTask(
            task_id=task_id,
            task_type="custom",
            query=task_name,
            status=TaskStatus.RUNNING,
            result_path=result_path,
            started_at=time.time(),
        )
        self._tasks[task_id] = task
        # 未来：调用对应skill执行
        return task_id

    # ─────────────────────────────────────────────────────────
    # 任务状态查询（非阻塞）
    # ─────────────────────────────────────────────────────────

    def check_task(self, task_id: str) -> Optional[SubAgentTask]:
        """检查单个任务状态"""
        if task_id not in self._tasks:
            return None
        task = self._tasks[task_id]
        if task.status == TaskStatus.RUNNING:
            self._refresh_task_status(task)
        return task

    def check_all_tasks(self) -> List[SubAgentTask]:
        """刷新所有任务状态"""
        for task in list(self._tasks.values()):
            self._refresh_task_status(task)
        return list(self._tasks.values())

    def get_done_tasks(self) -> List[SubAgentTask]:
        self.check_all_tasks()
        return [t for t in self._tasks.values() if t.status == TaskStatus.DONE]

    def _refresh_task_status(self, task: SubAgentTask):
        """刷新单个任务状态（检测是否完成）"""
        if task.status != TaskStatus.RUNNING:
            return

        # 检查结果文件
        if task.result_path:
            rp = Path(task.result_path)
            if rp.exists():
                task.status = TaskStatus.DONE
                task.finished_at = time.time()
                task.result = self._read_result(task.result_path)
                self._push_notification(task)
                return

        # 检查进程（如果有）
        # ...进程检查逻辑...

    def _read_result(self, result_path: str) -> SubAgentResult:
        p = Path(result_path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return SubAgentResult(
                    success=data.get("success", True),
                    findings=data.get("findings", ""),
                    output=data.get("output", ""),
                    scraped_count=data.get("scraped_count", 0),
                    files=data.get("files", []),
                )
            except Exception as e:
                return SubAgentResult(success=False, error=str(e))
        return SubAgentResult(success=False, error="结果文件不存在")

    # ─────────────────────────────────────────────────────────
    # 完成通知队列
    # ─────────────────────────────────────────────────────────

    def _push_notification(self, task: SubAgentTask):
        """任务完成，写入通知队列，并触发回调"""
        preview = ""
        if task.result and task.result.findings:
            preview = task.result.findings[:200]

        notif = CompletionNotification(
            task_id=task.task_id,
            task_type=task.task_type,
            query=task.query,
            success=task.result.success if task.result else True,
            result_path=task.result_path,
            findings_preview=preview,
            scraped_count=task.result.scraped_count if task.result else 0,
            error=task.result.error if task.result else None,
            finished_at=task.finished_at,
        )
        self._notification_queue.append(notif)
        print(f"\n[通知] 任务 {task.task_id} 已完成！结果: {task.result_path}\n")

        if self._notify_callback:
            try:
                self._notify_callback(notif)
            except Exception as e:
                print(f"[SubAgent] 通知回调失败: {e}")

    def get_and_clear_notifications(self) -> List[CompletionNotification]:
        """获取并清空通知队列（给母代理显示给用户）"""
        notifs = self._notification_queue.copy()
        self._notification_queue.clear()
        return notifs

    def set_notify_callback(self, callback: Callable[[CompletionNotification], None]):
        """注册通知回调（母代理调用，收到通知时执行）"""
        self._notify_callback = callback

    # ─────────────────────────────────────────────────────────
    # 子代理派发（核心修复：同一浏览器、不同窗口，零复制、零锁冲突）
    # ─────────────────────────────────────────────────────────

    async def spawn_subagent(self, query: str, task_type: str = "research") -> str:
        """
        启动子代理任务。

        正确做法（修复「复制 profile → 丢登录态 / SingletonLock 冲突」）：
        子代理**在母代理的进程内**运行，通过母代理的 BrowserManager 在同一个浏览器
        实例里开一个「新窗口」（新 page）。新窗口与母代理 page 相互独立，但共享同一个
        浏览器进程与持久化上下文，因此 cookies / 登录态 100% 继承，无需复制任何 profile，
        也不会有第二个浏览器实例抢 SingletonLock。
        """
        global _SUBAGENT_DEPTH
        # 硬限制：子代理内部不能再派生子代理（MAX_DEPTH=1）
        if _SUBAGENT_DEPTH >= 1:
            task_id = f"subagent_{int(time.time()*1000)}"
            task_dir = self.work_dir / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            task = SubAgentTask(
                task_id=task_id,
                task_type=task_type,
                query=query,
                status=TaskStatus.FAILED,
                result_path=str(task_dir / "result.json"),
                started_at=time.time(),
            )
            task.result = SubAgentResult(
                success=False,
                error="已达到子代理嵌套上限（MAX_DEPTH=1），禁止再派生子代理",
            )
            self._tasks[task_id] = task
            self._push_notification(task)
            return task_id

        task_id = f"subagent_{int(time.time()*1000)}"
        task_dir = self.work_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        task = SubAgentTask(
            task_id=task_id,
            task_type=task_type,
            query=query,
            status=TaskStatus.RUNNING,
            result_path=str(task_dir / "result.json"),
            started_at=time.time(),
        )
        self._tasks[task_id] = task

        # 后台运行（非阻塞，母代理立即拿到 task_id 继续）
        asyncio.create_task(self._run_subagent_in_process(task, query, task_type, task_dir))
        return task_id

    async def _run_subagent_in_process(self, task: SubAgentTask, query: str,
                                       task_type: str, task_dir: Path):
        """在母代理进程内、同一浏览器的子窗口中执行子代理任务。"""
        global _SUBAGENT_DEPTH
        _SUBAGENT_DEPTH += 1
        mother = self._mother_bm
        child_bm = None
        result_path = task.result_path  # 必须写入的 JSON 结果文件
        try:
            if mother is None:
                raise RuntimeError("母代理浏览器未就绪，无法派生子窗口（请先启动母代理浏览器）")

            # 1) 同一浏览器实例里开新窗口（共享登录态，零复制、零锁冲突）
            child_bm = await mother.spawn_child()

            work = task_dir / "work"
            work.mkdir(parents=True, exist_ok=True)

            from .session import DeepSeekSession
            from .commander import Commander

            # 2) 导航到聊天页并确认仍带着登录态（上下文共享，理应已登录）
            await child_bm.navigate()
            if not await child_bm.check_login():
                raise RuntimeError("子窗口未携带登录态（同一浏览器上下文共享异常，需排查）")

            # 3) 用子窗口的浏览器构建一个完整的子代理（自带 Commander 循环）
            session = DeepSeekSession(child_bm)
            commander = Commander(
                browser_manager=child_bm,
                session=session,
                work_dir=str(work),
            )
            await commander.start(session=session)

            # ── 增强版任务提示词：包含完整协议格式 + 工具说明 + 输出规范 ──
            task_prompt = (
                f"=== 子代理任务（类型：{task_type}） ===\n\n"
                f"任务描述：{query}\n\n"
                f"=== 协议格式（必须遵守） ===\n"
                f"所有工具调用必须用 @@@@ 包裹：\n"
                f"@@@@\n"
                f'{{"tool":"工具名","params":{{...}},"id":"唯一标识"}}\n'
                f"@@@@\n\n"
                f"=== 可用工具 ===\n"
                f"- file_write: 写入文件（参数 path, content）\n"
                f"- file_read: 读取文件（参数 path）\n"
                f"- shell_exec: 执行Shell命令（参数 command, timeout）\n"
                f"- docx_create: 生成Word文档（参数 path/filename, content）\n"
                f"- browser_search: 搜索网页（参数 query, max_pages）\n"
                f"- done: 任务完成，调用此工具结束\n\n"
                f"=== 执行要求 ===\n"
                f"1. 你是子代理，禁止再创建子代理（MAX_DEPTH=1）\n"
                f"2. 认真完成任务，产出保存到工作目录：{work}\n"
                f"3. 完成后必须调用 done() 工具汇报结果\n"
                f"4. 若生成文档/报告，用 docx_create 或 file_write 写到 {work}\n"
                f"5. 把你的核心发现和结论写在 done 之前的回复里（母代理会读取）\n"
            )

            reply = await commander.run_with_loop(
                user_instruction=task_prompt,
                file_path=None,
                context_hints=f"子代理任务（{task_type}）",
            )

            # 4) 收集产物文件（带内容摘要）
            files = []
            findings_parts = []
            for f in work.rglob("*"):
                if f.is_file():
                    try:
                        content_preview = f.read_text(encoding="utf-8", errors="ignore")[:500]
                    except Exception:
                        content_preview = "(二进制文件)"
                    files.append({
                        "path": str(f),
                        "name": f.name,
                        "size": f.stat().st_size,
                        "preview": content_preview,
                    })
                    # 文本类文件内容作为 findings
                    if f.suffix in (".txt", ".md", ".json", ".csv", ".html", ".xml"):
                        try:
                            full_content = f.read_text(encoding="utf-8", errors="ignore")
                            if len(full_content) > 50:  # 有实质内容的文件
                                findings_parts.append(f"[{f.name}]\n{full_content[:2000]}")
                        except Exception:
                            pass

            # 5) 构建结构化结果
            findings = "\n\n".join(findings_parts) if findings_parts else (reply or "")
            result_data = SubAgentResult(
                success=True,
                findings=findings,
                output=reply or "",
                files=files,
                scraped_count=len(files),
            )

            # ★★★ 关键修复：写入 result.json 到磁盘 ★★★
            # 母代理通过 check_task / wait_task 依赖此文件检测完成状态。
            # 不写此文件 → _refresh_task_status 永远认为任务在 RUNNING → 通信断裂。
            rp = Path(result_path)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps({
                "success": True,
                "findings": findings,
                "output": reply or "",
                "files": files,
                "scraped_count": len(files),
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            task.result = result_data
            task.status = TaskStatus.DONE
            print(f"[SubAgent] {task.task_id} 完成！结果已写入 {result_path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            task.status = TaskStatus.FAILED
            err_result = SubAgentResult(success=False, error=f"子代理执行异常: {e}")
            task.result = err_result
            print(f"[SubAgent] {task.task_id} 失败: {e}")
            # 失败也写 result.json，让母代理知道失败了
            try:
                rp = Path(result_path)
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(json.dumps({
                    "success": False,
                    "error": str(e),
                    "output": "",
                    "files": [],
                    "scraped_count": 0,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        finally:
            # 只关子代理自己的窗口，母代理浏览器保持运行
            if child_bm is not None:
                try:
                    await child_bm.close()
                except Exception:
                    pass
            _SUBAGENT_DEPTH -= 1
            self._push_notification(task)

    async def wait_task(self, task_id: str, timeout: float = 300.0) -> Optional[SubAgentTask]:
        """等待指定任务完成"""
        start = time.time()
        while time.time() - start < timeout:
            task = self.check_task(task_id)
            if task and task.status in (TaskStatus.DONE, TaskStatus.FAILED):
                return task
            await asyncio.sleep(2)
        return self.check_task(task_id)

    # ─────────────────────────────────────────────────────────
    # 任务摘要
    # ─────────────────────────────────────────────────────────

    def get_all_summary(self) -> str:
        """生成所有任务状态摘要"""
        self.check_all_tasks()
        lines = ["=== 子代理任务状态 ==="]
        for t in self._tasks.values():
            icon = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}[t.status.value]
            lines.append(f"{icon} [{t.task_id}] {t.task_type}: {t.query[:40]}")
        return "\n".join(lines) if lines else "暂无任务"

    def get_task_summary(self, task_id: str) -> str:
        task = self.check_task(task_id)
        if not task:
            return f"任务 {task_id} 不存在"
        icon = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}[task.status.value]
        info = f"{icon} {task.task_type} | {task.status.label}\n"
        info += f"查询: {task.query[:60]}\n"
        if task.status == TaskStatus.DONE and task.result:
            info += f"完成: 爬取 {task.result.scraped_count} 页，{len(task.result.findings)} 字"
        return info
