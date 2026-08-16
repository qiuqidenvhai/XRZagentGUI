"""
session.py — DeepSeek 会话管理
支持多平台、会话历史持久化和追溯
"""
import asyncio
import logging
import json
from typing import Optional, List, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("session")


# ============================================================
# 对话历史索引（全局持久化）
# ============================================================

# 对话历史索引（全局持久化）—— 全部落在 D 盘项目目录内，绝不写 C:\Users\...
from agent_core.xrz_paths import CONVERSATION_INDEX_PATH as _CONVERSATION_INDEX_PATH


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """单条消息"""
    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass
class ConversationRecord:
    """单次对话记录（含URL追溯）"""
    platform: str
    session_id: str
    url: str
    messages: List[Message]
    created_at: str
    tags: List[str] = None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "session_id": self.session_id,
            "url": self.url,
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "created_at": self.created_at,
            "tags": self.tags or [],
        }


class ConversationHistory:
    """对话历史管理器 - 持久化到 ~/.xianrenzhang_agent/conversation_index.json

    每次完成一轮或多轮对话后，自动调用 save() 持久化。
    用户可以通过 history.search(query) / history.get_by_url(url) 追溯历史。
    """

    def __init__(self):
        self._records: List[ConversationRecord] = []
        self._load_index()

    def _load_index(self):
        if _CONVERSATION_INDEX_PATH.exists():
            try:
                data = json.loads(_CONVERSATION_INDEX_PATH.read_text(encoding="utf-8"))
                for item in data:
                    msgs = [Message(role=m["role"], content=m["content"])
                            for m in item.get("messages", [])]
                    rec = ConversationRecord(
                        platform=item["platform"],
                        session_id=item["session_id"],
                        url=item["url"],
                        messages=msgs,
                        created_at=item["created_at"],
                        tags=item.get("tags", []),
                    )
                    self._records.append(rec)
                logger.info(f"已加载 {len(self._records)} 条对话历史")
            except Exception as e:
                logger.warning(f"加载对话历史失败：{e}")

    def _save_index(self):
        _CONVERSATION_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [rec.to_dict() for rec in self._records]
        _CONVERSATION_INDEX_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_record(self, platform: str, session_id: str, url: str,
                   messages: List[Message], tags: List[str] = None) -> ConversationRecord:
        """添加一条新对话记录"""
        rec = ConversationRecord(
            platform=platform,
            session_id=session_id,
            url=url,
            messages=messages,
            created_at=datetime.now().isoformat(),
            tags=tags or [],
        )
        self._records.append(rec)
        self._save_index()
        return rec

    def search(self, query: str, platform: str = None,
               tags: List[str] = None) -> List[ConversationRecord]:
        """全文搜索对话历史"""
        results = []
        for rec in self._records:
            if platform and rec.platform != platform:
                continue
            if tags and not any(t in rec.tags for t in tags):
                continue
            for msg in rec.messages:
                if query in msg.content:
                    results.append(rec)
                    break
        return results

    def get_by_url(self, url: str) -> Optional[ConversationRecord]:
        for rec in self._records:
            if rec.url == url:
                return rec
        return None

    def get_latest(self, platform: str = None) -> Optional[ConversationRecord]:
        records = [r for r in self._records if not platform or r.platform == platform]
        return max(records, key=lambda r: r.created_at) if records else None

    def list_records(self, platform: str = None, limit: int = 10) -> List[ConversationRecord]:
        records = [r for r in self._records if not platform or r.platform == platform]
        return records[-limit:]


# 全局单例
_conv_history: Optional[ConversationHistory] = None


def get_conversation_history() -> ConversationHistory:
    """获取全局对话历史单例"""
    global _conv_history
    if _conv_history is None:
        _conv_history = ConversationHistory()
    return _conv_history


# ============================================================
# 历史存档：方案二（消息 JSON 落盘）
# 与方案一（浏览器 URL 追溯）互为备份；启动时方案一失败自动回退到这里。
# ============================================================

def _task_conv_dir() -> Path:
    """方案二：每平台一个 JSON 对话文件的存放目录（落在 D 盘）"""
    from agent_core.xrz_paths import CONVERSATIONS_DIR as d
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest_conv_file(platform: str) -> Optional[Path]:
    """取某平台最新的对话 JSON 文件（用于方案二回退）"""
    d = _task_conv_dir()
    files = sorted(d.glob(f"conv_{platform}_*.json"), reverse=True)
    return files[0] if files else None


# ============================================================
# 历史任务索引：把每一次对话存成【一个独立任务】
# 与「一团历史自动加载」不同，这里每个任务有独立 id / 标题 / 文件，
# 可逐个列举、逐个恢复，用户不会看到「脚本替我预输了指令」。
# ============================================================

from agent_core.xrz_paths import TASKS_INDEX_PATH as _TASKS_INDEX_PATH  # 落在 D 盘 xrz_data


def _load_tasks_index() -> dict:
    if _TASKS_INDEX_PATH.exists():
        try:
            return json.loads(_TASKS_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"tasks": []}


def _save_tasks_index(idx: dict):
    _TASKS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TASKS_INDEX_PATH.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _first_user_text(messages) -> str:
    """从消息列表提取第一条 user 文本作为任务标题（兼容 Message / dict 两种结构）"""
    for m in messages:
        role = m.role if hasattr(m, "role") else m.get("role")
        content = m.content if hasattr(m, "content") else m.get("content", "")
        if role == "user" and content and content.strip():
            return content.strip()
    return "(无标题对话)"


def _record_task(platform: str, file_path: str, url: str, messages) -> str:
    """把一次对话作为【一个独立任务】写入任务索引，返回 task_id。

    同一 file 已存在则更新（去重），否则追加新任务。每次保存都生成一个
    独立、可列举、可单独恢复的任务条目。
    """
    idx = _load_tasks_index()
    tasks = idx.get("tasks", [])
    task_id = None
    for t in tasks:
        if t.get("file") == file_path:
            task_id = t["id"]
            t.update({
                "platform": platform,
                "url": url,
                "title": _first_user_text(messages)[:200],
                "updated_at": datetime.now().isoformat(),
            })
            break
    if task_id is None:
        task_id = (f"{platform}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                   f"_{abs(hash(file_path)) % 100000:05d}")
        tasks.append({
            "id": task_id,
            "platform": platform,
            "url": url,
            "title": _first_user_text(messages)[:200],
            "file": file_path,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        })
    idx["tasks"] = tasks
    _save_tasks_index(idx)
    return task_id


def _list_tasks(platform: str = None) -> list:
    """列举所有独立任务（可按平台过滤），按更新时间倒序。"""
    tasks = _load_tasks_index().get("tasks", [])
    if platform:
        tasks = [t for t in tasks if t.get("platform") == platform]
    tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return tasks


def _get_task(task_id: str) -> dict:
    for t in _load_tasks_index().get("tasks", []):
        if t.get("id") == task_id:
            return t
    return None


def list_all_tasks() -> list:
    """跨平台列举全部独立任务（供 GUI / API 展示历史对话列表用），按更新时间倒序。"""
    return _list_tasks(platform=None)


# ============================================================
# 会话配置
# ============================================================

class SessionConfig:
    def __init__(self, quick_mode: bool = True, model: str = "deepseek",
                 thinking_mode: bool = False):
        self.quick_mode = quick_mode
        self.model = model
        self.thinking_mode = thinking_mode


# ============================================================
# DeepSeek 会话管理
# ============================================================

class DeepSeekSession:
    """管理多轮对话上下文，支持会话历史自动持久化"""

    def __init__(self, browser_manager, config: Optional[SessionConfig] = None):
        self._bm = browser_manager
        self.config = config or SessionConfig()
        self._logged_in = False
        self._messages: List[Message] = []
        self._thinking_mode = False
        self._session_id = ""
        self._on_event = None  # 思考过程等事件回调 (event_type, data) -> None
        # 对话历史管理器
        self._history = get_conversation_history()
        # 平台标识 + 最近一次系统提示词（restore 后由 commander.start 重新插入）
        self._platform = "deepseek"
        self._system_prompt = ""
        # 【关键】agent 自身回复「不回灌」架构：
        # 每轮 agent 的实际动作-结果摘要存在这里，构建上下文时只回灌这个摘要，
        # 绝不把 agent 自己的原话再发回给模型（避免回声/污染/上下文膨胀）。
        # agent 若需回顾自身历史，调用 recall 工具从记忆里取。
        self._action_log: List[str] = []

    def set_on_event(self, cb):
        """设置事件回调，把「思考过程」等事件推给 GUI（参数: event_type, data）"""
        self._on_event = cb

    def _emit_thinking(self, text: str):
        if self._on_event:
            try:
                self._on_event("ai_thinking", {"text": text})
            except Exception:
                pass

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    @property
    def thinking_mode(self) -> bool:
        return self._thinking_mode

    @property
    def session_id(self) -> str:
        return self._session_id

    def toggle_thinking(self):
        """切换深度思考模式"""
        self._thinking_mode = not self._thinking_mode
        logger.info(f"思考模式切换为: {'深度思考' if self._thinking_mode else '快速模式'}")

    async def set_deep_think(self, enable: bool):
        """控制浏览器上的深度思考按钮"""
        if self._thinking_mode != enable:
            self._thinking_mode = enable
            await self._bm.toggle_deep_think(enable=enable)
            logger.info(f"深度思考模式 {'开启' if enable else '关闭'}")

    async def initialize(self):
        """初始化会话（检查/等待登录）"""
        if self._bm._browser is None:
            await self._bm.launch()
        await self._bm.navigate()
        self._logged_in = await self._bm.check_login()
        if not self._logged_in:
            logger.warning("DeepSeek 未登录，等待扫码...")
            self._logged_in = await self._bm.wait_login()
        else:
            logger.info("DeepSeek 已登录")
        await self._bm.save_cookies()

    def set_system_prompt(self, system_prompt: str):
        """设置系统提示词（同时缓存，便于 restore 后重新插入）"""
        self._system_prompt = system_prompt or ""
        for i, msg in enumerate(self._messages):
            if msg.role == "system":
                self._messages[i] = Message(role="system", content=system_prompt)
                logger.info("系统提示词已替换")
                return
        self._messages.insert(0, Message(role="system", content=system_prompt))
        logger.info("系统提示词已设置")

    async def send(self, text: str, attachments: list = None) -> str:
        """发送消息并获取回复（含自动历史持久化，支持 attachments 附件上传）"""
        if not self._logged_in:
            await self.initialize()

        # 纯协议消息（内部工具调用）
        if text.strip().startswith("@@@@"):
            sent = await self._bm._send_internal(text)
            if not sent:
                raise RuntimeError("内部消息发送失败")
            response = await self._bm.wait_response() or "（未收到回复）"
            self._messages.append(Message(role="assistant", content=response))
            return response

        # 常规消息
        self._messages.append(Message(role="user", content=text))

        # 附件上传（在发送文本前，保证文件预览出现在输入框）
        if attachments:
            try:
                res = await self._bm.upload_file(attachments)
                logger.info(f"附件上传: {res}")
            except Exception as e:
                logger.warning(f"附件上传失败: {e}")

        # 构建上下文
        full_context = self._build_context_for_send()
        sent = await self._bm.send_message(full_context)
        if not sent:
            raise RuntimeError("消息发送失败")

        await self._bm.save_cookies()

        response = await self._bm.wait_response(
            on_thinking=self._emit_thinking,
            thinking_selector="[class*='thinking'], [class*='reasoning'], [class*='思考']",
        )
        if response:
            self._messages.append(Message(role="assistant", content=response))
            await self._bm.save_cookies()
        else:
            response = "（未收到回复）"

        logger.info(f"对话完成，历史 {len(self._messages)} 条")

        # ===== 自动持久化 =====
        self._maybe_save_conversation()
        return response

    def _maybe_save_conversation(self):
        """每 5 轮或会话结束时自动持久化"""
        user_count = sum(1 for m in self._messages if m.role == "user")
        if user_count % 5 == 0 or user_count == 0:
            self._do_save_conversation()

    def _do_save_conversation(self):
        """实际执行持久化（同时更新方案一/方案二的落盘点）"""
        if not self._session_id:
            self._generate_session_id()
        url = self.get_current_url()
        # 方案一：URL 追溯 —— 把 URL + 消息写入全局索引
        self._history.add_record(
            platform=self._platform,
            session_id=self._session_id,
            url=url,
            messages=list(self._messages),
            tags=[],
        )
        # 方案二：消息 JSON —— 顺手落一份平台 JSON 备份（restore 时回退用）
        try:
            self._save_conv_json()
        except Exception as e:
            logger.warning(f"对话 JSON 备份失败（不影响方案一）: {e}")
        logger.info(f"对话已自动持久化 (session={self._session_id}, url={'有' if url else '无'})")

    def _save_conv_json(self, file_path: str = None) -> str:
        """方案二：把消息落盘成平台 JSON（URL 一并记下，便于交叉校验）"""
        if file_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = str(_task_conv_dir() / f"conv_{self._platform}_{ts}.json")
        data = {
            "platform": self._platform,
            "url": self.get_current_url(),
            "session_id": self._session_id,
            "messages": [{"role": m.role, "content": m.content} for m in self._messages],
        }
        Path(file_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 把这次对话登记为【一个独立任务】（标题=首条用户消息）
        _record_task(self._platform, file_path, self.get_current_url(), self._messages)
        return file_path

    def _generate_session_id(self):
        """生成唯一会话ID"""
        import hashlib
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snippet = "_".join([m.content[:30] for m in self._messages[:3]])
        self._session_id = hashlib.md5(f"{ts}_{snippet}".encode()).hexdigest()[:12]
        return self._session_id

    def _build_context_for_send(self) -> str:
        """构建完整上下文用于发送到浏览器

        【关键】系统提示词（===核心指令=== / @@@@ 协议 / 工具列表）必须随每轮
        『反复』发送给模型——这是操作协议正常运作的前提。

        【关键·去回声】agent 自己的自然语言回复【绝不】回灌给模型：
        - 不发送任何 role=="assistant" 的原话（避免回声 / 污染 / 上下文膨胀，
          也杜绝了「agent 读到自己的 UI 噪音后又混乱」这类问题）。
        - 改为发送一份紧凑的「执行进度」（来自 self._action_log），让模型把握
          全局动作-结果，而不被自己的长篇大论淹没。
        - 若 agent 需要回顾自己的判断/历史，应主动调用 recall 工具从记忆里取。
        """
        lines = []
        # 【关键修复】系统提示词（==== 协议 / 工具列表 / @@@@ 用法）必须每轮随消息发送。
        # 之前依赖 self._messages 里是否存在 role=="system" 的消息，但 DeepSeek 网页会话
        # 在 restore/reset 后 system 消息会丢失，导致模型完全收不到工具说明，于是坚称
        # 「我无法操作你的电脑」。这里改用缓存的 self._system_prompt 无条件前置，确保投递。
        if self._system_prompt:
            lines.append(f"[系统指令-你必须严格遵守]\n{self._system_prompt}")
        for msg in self._messages:
            if msg.role == "system":
                # 若消息列表里也有 system（重复），跳过避免冗余
                continue
            elif msg.role == "user":
                # 用户指令 + 系统回传的工具结果（这些是「外部输入」，需要保留）
                lines.append(f"[用户] {msg.content}")
            elif msg.role == "assistant":
                # 【去回声】agent 原话不回灌，本轮回馈内容整体跳过
                continue
        # 追加紧凑执行进度（动作-结果摘要），便于模型把握全局
        if self._action_log:
            lines.append("[执行进度]\n" + "\n".join(f"  - {a}" for a in self._action_log))
        return "\n\n".join(lines)

    def set_action_log(self, log: list):
        """由 commander 每轮写入最新的「动作-结果」摘要列表。"""
        self._action_log = list(log)

    def get_current_url(self) -> str:
        if self._bm and hasattr(self._bm, '_page') and self._bm._page:
            return self._bm._page.url
        return ""

    def save_conversation(self, file_path: str = None) -> str:
        """手动保存对话（方案二：消息 JSON）+ 同步更新方案一索引"""
        path = self._save_conv_json(file_path)
        # 同时刷新方案一（URL 索引），保证两套落盘点一致
        try:
            self._do_save_conversation()
        except Exception as e:
            logger.warning(f"刷新 URL 索引失败（JSON 已存）: {e}")
        logger.info(f"对话已保存到 {path}")
        return path

    def load_conversation(self, file_path: str) -> bool:
        """从文件加载对话历史（方案二）"""
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
            self._messages.clear()
            for m in data.get("messages", []):
                self._messages.append(Message(role=m["role"], content=m["content"]))
            # restore 后 commander.start 会用 set_system_prompt 重新插入协议
            logger.info(f"对话已从 {file_path} 加载，共 {len(self._messages)} 条消息")
            return True
        except Exception as e:
            logger.warning(f"加载对话失败：{e}")
            return False

    # ============================================================
    # 历史恢复：方案一(URL 追溯) 优先，失败自动回退方案二(消息 JSON)
    # ============================================================
    async def restore_conversation(self, task_id: str = None) -> bool:
        """恢复一个【独立任务】的历史对话（手动触发，绝不自动执行）。

        - task_id 给定：恢复该指定任务（从任务索引取文件加载）。
        - task_id 为 None：恢复最新一个任务（兼容旧行为，但仍需用户显式调用）。
        方案一(URL 追溯) 优先，失败自动回退方案二(消息 JSON)。
        返回是否成功恢复。
        """
        if task_id:
            t = _get_task(task_id)
            if t and t.get("file") and self.load_conversation(t["file"]):
                logger.info(f"历史恢复：指定任务 {task_id} 成功")
                return True
            logger.warning(f"未找到任务 {task_id}，回退到最新任务")
        # 方案一优先
        if await self._restore_from_url():
            logger.info("历史恢复：方案一(URL 追溯) 成功")
            return True
        # 方案二回退
        if self._restore_from_json():
            logger.info("历史恢复：回退方案二(消息 JSON) 成功")
            return True
        logger.info("历史恢复：无历史可恢复（全新会话）")
        return False

    def list_tasks(self) -> list:
        """列举本平台的全部独立任务（按更新时间倒序）"""
        return _list_tasks(platform=self._platform)

    async def _restore_from_url(self) -> bool:
        """方案一：URL 追溯。导航到上次对话 URL 并从浏览器读回消息。"""
        try:
            rec = self._history.get_latest(platform=self._platform)
        except Exception:
            return False
        if not rec or not rec.url:
            return False
        try:
            await self._bm.navigate(rec.url)
            if not await self._bm.check_login():
                logger.warning("URL 追溯：导航后未登录，回退方案二")
                return False
            msgs = await self._read_existing_messages()
            if msgs:
                self._messages = msgs
                return True
        except Exception as e:
            logger.warning(f"URL 追溯失败，回退方案二: {e}")
        return False

    def _restore_from_json(self) -> bool:
        """方案二：从最新平台 JSON 文件加载消息。"""
        path = _latest_conv_file(self._platform)
        if path and self.load_conversation(str(path)):
            return True
        return False

    async def _read_existing_messages(self) -> List[Message]:
        """从浏览器 DOM 读回已有对话（方案一用）。

        读所有消息气泡文本，按出现顺序交替标记为 user/assistant（对话通常
        以 user 起头）。若读不到任何内容返回空列表。
        """
        if not (self._bm and getattr(self._bm, "_page", None)):
            return []
        try:
            js = (
                "() => {"
                " const sels = ['.message_content', \"[class*='message_content']\","
                " \"[class*='msg_content']\", '.bubble', \"[class*='bubble']\","
                " \"[data-role='user']\", \"[class*='message']\"];"
                " let texts = [];"
                " for (const sel of sels) {"
                "   const els = document.querySelectorAll(sel);"
                "   for (const el of els) {"
                "     const t = (el.innerText || '').trim();"
                "     if (t) texts.push(t);"
                "   }"
                "   if (texts.length) break;"
                " }"
                " return texts;"
                "}"
            )
            texts = await self._bm._page.evaluate(js)
            msgs = []
            for i, t in enumerate(texts):
                role = "user" if i % 2 == 0 else "assistant"
                msgs.append(Message(role=role, content=t))
            return msgs
        except Exception as e:
            logger.warning(f"从浏览器读回对话失败: {e}")
            return []

    def clear_history(self):
        """清空当前会话历史"""
        self._messages.clear()
        self._session_id = ""

    async def start_new_conversation(self):
        """开一个全新的独立对话：清空本地上下文 + 在浏览器里点「新对话」。

        这样下一次保存会自动登记成一个新的独立任务（不会和旧任务混在一起）。
        """
        self.clear_history()
        try:
            if self._bm and getattr(self._bm, "new_session", None):
                await self._bm.new_session()
        except Exception as e:
            logger.warning(f"新建对话（浏览器侧）失败: {e}")

    def rebuild_context_prompt(self) -> str:
        """重建上下文提示（最近20条）"""
        recent = self._messages[-20:]
        lines = []
        for msg in recent:
            role = "用户" if msg.role == "user" else "助手"
            lines.append(f"「{role}」{msg.content[:300]}")
        return "\n".join(lines)
