"""
session.py — DeepSeek 会话管理
支持多平台、会话历史持久化和追溯
"""
import asyncio
import logging
import json
import re as _re
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
    internal: bool = False  # True = Agent 内部控制轮（工具结果/协议纠正），不写进历史


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
        """保存对话索引到文件，失败时记录警告但不中断流程"""
        try:
            _CONVERSATION_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = [rec.to_dict() for rec in self._records]
            _CONVERSATION_INDEX_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except PermissionError:
            # 文件被占用时跳过保存，下次保存时会重试
            logger.warning(f"对话索引写入被拒绝（文件可能被占用）: {_CONVERSATION_INDEX_PATH}")
        except Exception as e:
            logger.warning(f"保存对话索引失败：{e}")

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


# ─── 任务上下文：当前任务 id 及其「临时文件 / 记忆」登记表 ───
# 删除任务时的清理策略（用户明确要求）：
#   ✅ 清理：该任务产生的临时文件（缓冲区/中间产物/调试截图）与记忆文件
#   ❌ 保留：任务产物文件（交付物，删了会丢工作成果）
# 由于产物与临时文件混放在同一目录、且文件名不带任务标识，无法事后靠路径
# 区分，因此改为【登记制】：任务进行中把临时资源登记到该任务条目下，
# 删除时只清理登记过的资源，确保绝不误删产物。
_CURRENT_TASK_ID: Optional[str] = None


def set_current_task_id(task_id: Optional[str]):
    """设置当前活动任务 id（新建 / 恢复任务时调用）"""
    global _CURRENT_TASK_ID
    _CURRENT_TASK_ID = task_id or None


def get_current_task_id() -> Optional[str]:
    """获取当前活动任务 id"""
    return _CURRENT_TASK_ID


def _update_task_field(task_id: Optional[str], field: str, value, unique: bool = True):
    """往任务条目里的某个列表字段追加一个值（默认去重）"""
    if not task_id:
        return
    idx = _load_tasks_index()
    changed = False
    for t in idx.get("tasks", []):
        if t.get("id") == task_id:
            lst = t.setdefault(field, [])
            if not unique or value not in lst:
                lst.append(value)
                changed = True
            break
    if changed:
        _save_tasks_index(idx)


def register_temp_file(path, task_id: Optional[str] = None):
    """登记一个临时文件到当前（或指定）任务名下 —— 删除任务时会被清理。

    只登记【临时/中间】文件，切勿登记产物文件。
    """
    _update_task_field(task_id or _CURRENT_TASK_ID, "temp_files", str(path))


def register_memory_id(mem_id: str, task_id: Optional[str] = None):
    """登记一条记忆 id 到当前（或指定）任务名下 —— 删除任务时会被清理。"""
    _update_task_field(task_id or _CURRENT_TASK_ID, "memory_ids", mem_id)


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
    # 登记为「当前活动任务」：后续产生的临时文件 / 记忆都挂到这个 id 名下，
    # 删除任务时才能按登记精准清理（而不是靠猜路径，更不会误删产物）。
    set_current_task_id(task_id)
    return task_id


def _list_tasks(platform: str = None) -> list:
    """列举所有独立任务（可按平台过滤），按更新时间倒序。"""
    tasks = _load_tasks_index().get("tasks", [])
    if platform:
        tasks = [t for t in tasks if t.get("platform") == platform]
    tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return tasks


def _get_task(task_id: str) -> dict:
    """按 id 查任务。带 id 变体容忍（2026-09-25 真机实证）：

    GUI/热补丁层用的会话 id 是 `deepseek_20260925_223100`（会话文件
    conv_<id>.json 风格，无随机尾），而任务索引里的 id 是
    `deepseek_20260925_222458_62771`（带 5 位随机尾）。旧实现只做精确
    匹配 → 恢复旧任务时必 miss → 静默「回退到最新任务」，用户点历史
    任务恢复会载错会话。现在双向前后缀 + conv 文件名兜底匹配。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return None
    tasks = _load_tasks_index().get("tasks", [])
    # 1) 精确匹配
    for t in tasks:
        if t.get("id") == tid:
            return t
    # 2) 前后缀变体：tid 是索引 id 去掉随机尾（或反过来）
    for t in tasks:
        i = str(t.get("id") or "")
        if i.startswith(tid + "_") or tid.startswith(i + "_"):
            return t
    # 3) conv 文件名兜底：任务 file 是 conv_<平台id>.json，比对 basename
    for t in tasks:
        f = str(t.get("file") or "")
        base = f.replace("\\", "/").rsplit("/", 1)[-1]
        if base in (f"conv_{tid}.json", f"{tid}.json"):
            return t
    return None


def _delete_task(task_id: str) -> bool:
    """删除指定任务，返回是否成功。

    清理范围（按用户要求）：
      ✅ 对话 JSON（任务本体）
      ✅ 该任务登记的临时文件（缓冲区、中间文件、记忆摘要 md 等）
      ✅ 该任务产生的记忆条目
      ❌ 产物文件 —— 一律保留，绝不删除（交付物，删了会丢工作成果）

    注意：旧的 `GUI_SESSION_DIR / task_id` 清理是死代码 —— 任务产物直接散落在
    gui_session 根目录、文件名不含任务 id，该路径永远不存在，所以残留一直没被清掉。
    现改为【登记制】：只清理任务条目里登记过的资源，精准且不误伤产物。
    """
    import shutil

    idx = _load_tasks_index()
    target = None
    for t in idx.get("tasks", []):
        if t.get("id") == task_id:
            target = t
            break
    if target is None:
        return False

    # 1) 对话记录本身
    file_path = target.get("file", "")
    if file_path and Path(file_path).exists():
        try:
            Path(file_path).unlink()
            logger.info(f"已删除任务对话文件: {file_path}")
        except BaseException as e:  # noqa: BLE001 删除拦截可能抛 SystemExit，不能让它逃逸
            logger.warning(f"删除任务对话文件失败: {type(e).__name__}: {e}")

    # 2) 该任务登记的临时文件（未登记的产物文件一律不碰）
    removed = 0
    for p in target.get("temp_files", []):
        try:
            fp = Path(p)
            if fp.is_file():
                fp.unlink()
                removed += 1
            elif fp.is_dir():
                shutil.rmtree(fp, ignore_errors=True)
                removed += 1
        except BaseException as e:  # noqa: BLE001 同上：拦截删除的异常可能是 BaseException
            logger.warning(f"删除临时文件失败 {p}: {type(e).__name__}: {e}")
    if removed:
        logger.info(f"已清理任务 {task_id} 的 {removed} 个临时文件")

    # 3) 该任务产生的记忆（按 id 列表 + 按 task_id 双重清理，兼容新旧数据）
    try:
        from agent_core.xrz_paths import GUI_SESSION_DIR
        from agent_core.memory_manager import MemoryManager
        mm = MemoryManager(str(GUI_SESSION_DIR))
        n = mm.delete_entries(target.get("memory_ids", []))
        n += mm.delete_by_task(task_id)
        if n:
            logger.info(f"已清理任务 {task_id} 的 {n} 条记忆")
    except Exception as e:
        logger.warning(f"清理任务记忆失败: {e}")

    # 4) 从索引移除
    idx["tasks"] = [t for t in idx["tasks"] if t.get("id") != task_id]
    _save_tasks_index(idx)

    # 若删掉的正是当前活动任务，清空上下文，避免后续登记挂到已删除的任务上
    if get_current_task_id() == task_id:
        set_current_task_id(None)
    return True


# 公共接口
delete_task = _delete_task


def list_all_tasks() -> list:
    """跨平台列举全部独立任务（供 GUI / API 展示历史对话列表用），按更新时间倒序。"""
    return _list_tasks(platform=None)


# 「思考过程」折叠条标题行（DeepSeek/千问/豆包等平台在推理正文前渲染的那一行）。
# 实测形态：「已思考（用时 1 秒）」「深度思考（用时 12 秒）」「已完成思考」等。
# 注意：只匹配【整行就是标题】或【标题紧跟换行】的情形，绝不匹配行内出现的同名字样
# ——否则会把模型正文里正常引用这句话的句子也剪掉（第一版就犯了这个错）。
_THINK_HEADER_LINE = _re.compile(
    r"^[\s]*(?:已完成|已深度|已|完成)?(?:深度)?思考\s*(?:[（(][^）)\n]{0,20}[）)])?[\s]*$",
    _re.M)


def _strip_thinking_header(text: str) -> str:
    """剥掉模型回复里混进来的「已思考（用时 N 秒）」折叠条标题。

    为什么要在一个这么窄的地方下功夫：这行字是平台 UI 渲染出来的，不是模型写的内容。
    一旦被读进 assistant 内容，就会①落盘污染历史，②下一轮作为上下文回灌给模型，
    让模型以为自己上一轮说过这句话。真机已复现（会话 json 开头就是它）。

    实现要点（踩过两次坑后的定稿）：
      · 逐行判断，只删「整行恰好是标题」的行 —— 天然不会误伤正文里引用该字样的句子；
      · 逐行处理时，若发现某行【以标题开头且后面还有正文】，只切掉标题那一段；
      · 全删光时返回空串（调用方负责决定是否回退），不能反过来把原文还回去，
        否则「回复内容就只有一行标题」这种最该清理的情况反而清不掉。
    """
    if not text:
        return text
    try:
        kept = []
        for line in text.split("\n"):
            if _THINK_HEADER_LINE.match(line):
                continue   # 整行都是标题 → 丢弃
            # 标题与正文挤在同一行：剥掉开头的标题片段，保留正文
            m = _re.match(
                r"[\s]*(?:已完成|已深度|已|完成)?(?:深度)?思考[（(]用时[^）)]{0,20}[）)]"
                r"\s*(?=\S)",
                line)
            if m:
                line = line[m.end():]
            kept.append(line)
        out = _re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        return out
    except Exception:
        return text


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

async def wait_conversation_loaded(bm, url: str = "") -> bool:
    """跳回原对话网址后，等网页【真正把该会话动态渲染出来】。

    【用户要求：按动态渲染判定，不自己掐表放弃】
    - wait_for_load_state：等主文档就绪（Playwright 原生，随页面实际加载推进）
    - wait_for_function：轮询真实 DOM，直到「URL 到位 + 出现输入框/消息节点」
      返回真才继续；网页每渲染一帧都会重新判定，慢网/慢渲染就多等，不做固定秒数截断。
    旧实现 navigate 完立刻 check_login，SPA 还没渲染出 textarea → has_textarea=False
    → 误判未登录 → 白白放弃网址路线，直接掉进本地日志。
    """
    page = getattr(bm, "_page", None)
    if page is None:
        return False
    key = (str(url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "")
    # ① 等主文档就绪（动态，由页面加载状态驱动）
    try:
        await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    # ② 等 SPA 把会话渲染出来（动态轮询真实 DOM）
    js = """(key) => {
        const sels = ["textarea", "[contenteditable='true']",
                      "[class*='message']", "[class*='msg']", "[class*='bubble']"];
        let n = 0;
        for (const s of sels) n += document.querySelectorAll(s).length;
        const u = location.href || '';
        return (!key || u.indexOf(key) >= 0) && n > 0;
    }"""
    try:
        await page.wait_for_function(js, arg=key)
        return True
    except Exception:
        return False


async def restore_via_url(bm, url: str, read_msgs):
    """方案一：跳回该任务的原始对话网址 → 等网页动态渲染 → 从页面读回消息。

    【用户明确要求】恢复对话必须「先找回原来的对话网址」，只有这条路走不通
    （页面确实渲染不出来 / 读不到消息 / 未登录）才回退方案二发本地对话日志。
    返回读回的消息列表；失败返回 False/[]。
    """
    try:
        await bm.navigate(url)
    except Exception as e:
        logger.warning(f"URL 追溯：导航失败: {e}")
        return False
    if not await wait_conversation_loaded(bm, url):
        logger.warning("URL 追溯：网页未渲染出该会话，回退方案二(本地日志)")
        return False
    try:
        if not await bm.check_login():
            logger.warning("URL 追溯：渲染后仍未登录，回退方案二(本地日志)")
            return False
    except Exception as e:
        logger.warning(f"URL 追溯：登录检查异常: {e}")
        return False
    try:
        msgs = await read_msgs()
    except Exception as e:
        logger.warning(f"URL 追溯：读回页面消息失败: {e}")
        return False
    return msgs or False

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
        # 【修复·原生多轮（2026-09-24）】照 platform_browser.py 的 _native_multi_turn_seeded
        # 修法：系统提示词只在【首轮】完整播种一次，后续轮只发几百字的轻量格式提醒，
        # 靠 DeepSeek 网页在同一会话里的原生多轮记忆续上下文。旧实现每轮都无条件把
        # 6~7KB [系统指令] 重塞进 user 文本 → ①网页限流「发送过于频繁」②模型把指令
        # 复读回当回复（污染子代理 output）③conv 里系统指令堆 6 次。故加此播种开关。
        self._proto_seeded = False
        # 【修复】整个会话复用同一个会话 JSON：以前每次保存都新建时间戳文件，
        # 一次任务会在历史里刷出几十条重复记录。
        self._conv_file_path = ""

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

    async def send(self, text: str, attachments: list = None,
                   internal: bool = False, stop_check=None) -> str:
        """发送消息并获取回复（含自动历史持久化，支持 attachments 附件上传）

        internal=True 表示 Agent 内部控制轮（工具结果回传 / 协议纠正 / 继续指令），
        不写进对话历史文件，避免历史记录被 [系统] 工具…执行结果 这类噪音淹没。
        stop_check：可调用（无参），返回 True 时中止等待 AI 回复（让「⏹ 中断 / 💬 插话」
        在等待期间立即生效，不用等到下一轮循环顶部才检测）。
        """
        if not self._logged_in:
            await self.initialize()

        # 纯协议消息（内部工具调用）
        if text.strip().startswith("@@@@"):
            sent = await self._bm._send_internal(text)
            if not sent:
                raise RuntimeError("内部消息发送失败")
            response = await self._bm.wait_response(
                stop_check=stop_check,
            ) or "（未收到回复）"
            self._messages.append(Message(role="assistant", content=response))
            return response

        # 常规消息
        self._messages.append(Message(role="user", content=text, internal=bool(internal)))

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

        # 【2026-09-21 真机修复·思考过程显示为空的根因】
        # 这里原来写死了 thinking_selector="[class*='thinking'], [class*='reasoning'],
        # [class*='思考']" —— 三个 pattern 全都不匹配 DeepSeek 的真实思考面板 class
        # `.ds-think-content`（真机 /probe 实测：0 匹配）。后果：整个任务过程中
        # ai_thinking 事件一条都不发 → GUI 的「🧠 模型推理（深度思考）」块永远空白
        # （用户报「思考过程显示有大问题」）。
        # 正确做法：优先用平台 profile 里配置的 thinking_selector（platforms.json 已按
        # 真机 DOM 逐个平台核对过），只有平台没配时才回落到通用兜底 pattern。
        # 注意：self._bm 可能是 PlatformBrowserManager（有 .profile），也可能是
        # browser.py 的 BrowserManager（没有 .profile）——必须用 getattr 兜住，
        # 否则会抛 `'BrowserManager' object has no attribute 'profile'` 把整轮打挂。
        _think_sel = ""
        try:
            _prof = getattr(self._bm, "profile", None)
            _think_sel = (getattr(_prof, "thinking_selector", "") or "").strip()
        except Exception:
            _think_sel = ""
        if not _think_sel:
            _think_sel = "[class*='thinking'], [class*='reasoning'], [class*='think'], [class*='思考']"

        response = await self._bm.wait_response(
            on_thinking=self._emit_thinking,
            thinking_selector=_think_sel,
            stop_check=stop_check,
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
        """每轮都持久化。

        【修复】以前是「每 5 条用户消息才存一次」，结果绝大多数任务只有 1 条用户消息
        → 任务跑完根本不落盘，历史里啥也没有；而自愈清空上下文后 user_count 变 0，
        又变成「每轮都新建文件」→ 一次任务刷出几十条历史。
        现在会话 JSON 路径固定复用，每轮覆盖写同一个文件，既不会漏存也不会刷屏。
        """
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
            messages=self._visible_messages(),
            tags=[],
        )
        # 方案二：消息 JSON —— 顺手落一份平台 JSON 备份（restore 时回退用）
        try:
            self._save_conv_json()
        except Exception as e:
            logger.warning(f"对话 JSON 备份失败（不影响方案一）: {e}")
        logger.info(f"对话已自动持久化 (session={self._session_id}, url={'有' if url else '无'})")

    def _visible_messages(self) -> list:
        """过滤掉内部轮（工具结果/协议纠正）和系统提示词，只留用户真实说的话 + AI 回复。

        【修复】系统提示词（6~7KB 的工具协议）以前也会被写进会话 JSON，
        导致历史文件里第一条永远是整篇提示词，回看时被一大段指令糊脸。

        【2026-09-21 修复·思考标题污染】DeepSeek 的思考面板折叠条会渲染
        「已思考（用时 1 秒）」这样一行，实测被整段读进 assistant 内容里落盘
        （真机验证：conv_deepseek_*.json 的 assistant 内容开头就是它）。
        这里统一在「落盘前的唯一收口」剥掉该行，两个落盘点（URL 索引 + JSON 备份）
        都走这个函数，改一处即可全覆盖。
        """
        out = []
        for m in self._messages:
            if getattr(m, "internal", False) or m.role == "system":
                continue
            content = m.content
            if m.role == "assistant" and isinstance(content, str) and content:
                content = _strip_thinking_header(content)
            out.append(Message(role=m.role, content=content,
                               internal=getattr(m, "internal", False)))
        return out

    def _save_conv_json(self, file_path: str = None) -> str:
        """方案二：把消息落盘成平台 JSON（URL 一并记下，便于交叉校验）

        只写用户真实对话：内部工具结果轮不落盘。
        """
        if file_path is None:
            # 同一会话固定一个文件：历史里一条任务只占一行，且标题能取到用户原话
            if not getattr(self, "_conv_file_path", ""):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._conv_file_path = str(_task_conv_dir() / f"conv_{self._platform}_{ts}.json")
            file_path = self._conv_file_path
        else:
            self._conv_file_path = file_path
        data = {
            "platform": self._platform,
            "url": self.get_current_url(),
            "session_id": self._session_id,
            "messages": [{"role": m.role, "content": m.content}
                         for m in self._visible_messages()],
        }
        Path(file_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 把这次对话登记为【一个独立任务】（标题=首条用户消息）
        _record_task(self._platform, file_path, self.get_current_url(),
                     self._visible_messages())
        return file_path

    def _generate_session_id(self):
        """生成唯一会话ID"""
        import hashlib
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snippet = "_".join([m.content[:30] for m in self._messages[:3]])
        self._session_id = hashlib.md5(f"{ts}_{snippet}".encode()).hexdigest()[:12]
        return self._session_id

    _COMPACT_PROTOCOL_CARD = (
        "【格式提醒】本会话继续：你只能用 @@@@ JSON 协议回复，格式 "
        "@@@@{\"tool\":\"工具名\",\"params\":{...},\"id\":\"1\"}@@@；"
        "从首轮已列出的工具集里选（file_write / file_edit / file_read / "
        "docx_create / pptx_create / browser_search / shell_exec / done 等），"
        "不要用网页内建功能生成文件。需要直接执行系统命令用 "
        "<<<RAW>>> 命令 <<<RAW>>>。全部做完后调用 done 收尾。"
    )

    def _compact_protocol_card(self) -> str:
        """首轮随完整协议一起发的「格式提醒」（紧贴任务前，弱模型也看得到）。"""
        return (
            "[格式提醒·必须遵守] 你只能用下面这种 @@@@ JSON 格式回复，"
            "不要用自然语言描述你要做什么：\n"
            "@@@@\n"
            '{"tool":"工具名","params":{...},"id":"1"}\n'
            "@@@@\n"
            "例如写文件：@@@@\n"
            '{"tool":"file_write","params":{"path":"a.txt","content":"你好"},"id":"1"}\n'
            "@@@@\n"
            "任务全部做完后，用：@@@@\n"
            '{"tool":"done","params":{},"id":"9"}\n'
            "@@@@"
        )

    def _build_context_for_send(self) -> str:
        """构建完整上下文用于发送到浏览器。

        【原生多轮修复·2026-09-24，照 platform_browser.py 的 _native_multi_turn_seeded 修法】
        区分首轮 vs 后续轮：
          - 首轮（_proto_seeded 为 False）：完整播种 [系统指令]（工具列表+核心协议）
            + 本轮用户消息 + 格式提醒 + 执行进度。这是把协议一次性喂进网页。
          - 后续轮（_proto_seeded 为 True）：只发「轻量格式提醒 + 本轮用户消息 + 执行进度」，
            靠 DeepSeek 网页在同一会话里的原生多轮记忆续上下文，【绝不】再重打包
            ~6.8KB 系统指令。

        旧实现每轮都无条件整段重发 [系统指令]（725 行旧代码），导致：①网页限流
        「任务发送过于频繁」；②模型把 6.8KB 指令复读回来当回复（污染子代理 output，
        母代理读不到结论）；③conv 里 [系统指令] 堆 6 次。改用原生多轮后，短轮就是短输入，
        多轮才真正续得上，且不会再触发网页限流。
        """
        lines = []
        user_msgs = [m for m in self._messages if m.role == "user"]
        # send() 已把本轮新消息 append 到 _messages 末尾，最后一条 user 即本轮新增
        last_user = user_msgs[-1].content if user_msgs else ""

        if not self._proto_seeded:
            # ── 首轮：完整播种 ──
            if self._system_prompt:
                lines.append(f"[系统指令]\n{self._system_prompt}")
            if last_user:
                lines.append(f"[用户] {last_user}")
            lines.append(self._compact_protocol_card())
            self._proto_seeded = True
        else:
            # ── 后续轮：轻量提醒，吃网页原生多轮记忆，不再重发系统指令 ──
            lines.append(self._COMPACT_PROTOCOL_CARD)
            if last_user:
                lines.append(f"[用户] {last_user}")

        # 追加紧凑执行进度（动作-结果摘要），让模型把握全局（短，不膨胀）
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
            # 恢复的是哪个文件，后续就继续写回这个文件（否则恢复后一保存又建新文件）
            self._conv_file_path = file_path
            # 【原生多轮】恢复历史任务 = 浏览器侧重新加载该会话，系统指令需重新完整播种一次
            self._proto_seeded = False
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
        # 【原生多轮】恢复 = 浏览器侧重新载入该会话，系统指令需重新完整播种一次
        # （load_conversation 已重置，这里统一兜底方案一 URL 追溯路径）。
        self._proto_seeded = False
        if task_id:
            t = _get_task(task_id)
            if not t:
                logger.warning(f"未找到任务 {task_id}，回退到最新任务")
            else:
                url = str(t.get("url") or "")

                # ── 方案一（优先，用户明确要求）：跳回该任务的原始对话网址，
                #    让网页自己把那轮对话渲染回来，再从页面读回消息 ────────────
                if url and ("http" in url) and self._bm and getattr(self._bm, "navigate", None):
                    try:
                        msgs = await restore_via_url(self._bm, url, self._read_existing_messages)
                        if msgs:
                            self._messages = msgs
                            # 恢复的是哪个任务，后续就继续写回该任务文件
                            self._conv_file_path = str(t.get("file") or "")
                            self._proto_seeded = False
                            set_current_task_id(task_id)
                            logger.info(
                                f"历史恢复：方案一(URL 追溯) 成功 {task_id} "
                                f"（网页已跳回并读回 {len(msgs)} 条消息）{url}")
                            return True
                        logger.warning("URL 追溯未取到消息，回退方案二(本地日志)")
                    except Exception as e:
                        logger.warning(f"URL 追溯失败，回退方案二(本地日志): {e}")

                # ── 方案二（兜底）：只有网址路线失败，才发本地对话日志 ──────────
                if t.get("file") and self.load_conversation(t["file"]):
                    set_current_task_id(task_id)
                    logger.info(
                        f"历史恢复：回退方案二(本地日志) 成功 {task_id} "
                        f"（本地日志已载入当前会话，网页未跳回原网址）")
                    return True
                logger.warning(f"任务 {task_id} 的本地日志文件也不可用，回退到最新任务")
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
            msgs = await restore_via_url(self._bm, rec.url, self._read_existing_messages)
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
        # 【原生多轮】开全新会话：网页侧无历史记忆，必须重新完整播种系统指令。
        self._proto_seeded = False

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
