"""
memory_manager.py — 记忆系统
- 自动摘要：每 N 轮对话提醒用户整理
- 持久化：摘要存入记忆文件
- 检索：按需输出历史记忆
"""
import json
import time
import logging
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger("memory")


@dataclass
class MemorySummary:
    timestamp: str
    conversation_id: str
    turn_count: int
    summary: str
    key_decisions: List[str]
    pending_tasks: List[str]


class MemoryManager:
    """
    记忆管理器
    - 每 N 轮自动触发摘要提醒（通过 commander 发出提示）
    - 摘要持久化到 work_dir/memory/
    - 支持按任务名检索历史
    """

    DEFAULT_SUMMARY_INTERVAL = 10  # 每10轮触发一次摘要提醒

    def __init__(self, work_dir: str):
        self.work_dir = Path(work_dir)
        self.memory_dir = self.work_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.turn_count = 0
        self.conversation_counter = 0
        self.current_conversation_id = ""
        self.current_task_name = ""
        self.last_summary_turn = 0
        self.summary_interval = self.DEFAULT_SUMMARY_INTERVAL

        # 当前会话的原始消息（用于生成摘要）
        self._messages: List[dict] = []

    # ----------------------------------------------------------
    # 对话轮次管理
    # ----------------------------------------------------------
    def new_conversation(self, task_name: Optional[str] = None) -> str:
        """开启新对话，返回任务名"""
        self.conversation_counter += 1
        if task_name:
            self.current_task_name = task_name
        else:
            self.current_task_name = f"任务{self.conversation_counter}"
        self.current_conversation_id = f"{int(time.time())}_{self.conversation_counter}"
        self.turn_count = 0
        self.last_summary_turn = 0
        self._messages.clear()
        return self.current_task_name

    def add_turn(self, user_msg: str, ai_msg: str):
        """记录一轮对话"""
        self._messages.append({
            "turn": self.turn_count + 1,
            "user": user_msg[:500],
            "ai": ai_msg[:1000],
            "time": time.strftime("%H:%M:%S"),
        })
        self.turn_count += 1

    def should_summarize(self) -> bool:
        """是否应该触发摘要"""
        return (self.turn_count - self.last_summary_turn) >= self.summary_interval

    def get_unsummarized_turns(self) -> List[dict]:
        """获取未摘要的轮次"""
        return self._messages[self.last_summary_turn:]

    def get_pending_text(self) -> str:
        """生成待摘要的文本（给 AI 整理用）"""
        turns = self.get_unsummarized_turns()
        if not turns:
            return ""
        lines = []
        for t in turns:
            lines.append(f"--- 轮次 {t['turn']} ---")
            lines.append(f"用户: {t['user']}")
            lines.append(f"AI: {t['ai']}")
        return "\n".join(lines)

    # ----------------------------------------------------------
    # 摘要持久化
    # ----------------------------------------------------------
    def save_summary(self, summary_text: str, decisions: List[str], tasks: List[str]) -> str:
        """保存摘要到文件，返回文件路径"""
        meta = MemorySummary(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            conversation_id=self.current_conversation_id,
            turn_count=self.turn_count,
            summary=summary_text,
            key_decisions=decisions,
            pending_tasks=tasks,
        )

        filename = f"{self.current_task_name}_摘要.md"
        filepath = self.memory_dir / filename

        content = f"""# {self.current_task_name} — 记忆摘要

**时间**: {meta.timestamp}
**对话轮次**: {meta.turn_count}
**摘要**: {meta.summary}

## 关键决策
{chr(10).join(f"- {d}" for d in meta.key_decisions) if meta.key_decisions else "(无)"}

## 待办/未完成
{chr(10).join(f"- {t}" for t in meta.pending_tasks) if meta.pending_tasks else "(无)"}

## 对话记录（最近 {len(self._messages)} 轮）
"""
        for t in self._messages[-20:]:
            content += f"\n### 轮次 {t['turn']} ({t['time']})\n**用户**: {t['user']}\n**AI**: {t['ai']}\n"

        filepath.write_text(content, encoding="utf-8")
        self.last_summary_turn = self.turn_count
        logger.info(f"摘要已保存: {filepath}")
        return str(filepath)

    # ----------------------------------------------------------
    # 历史检索
    # ----------------------------------------------------------
    def list_summaries(self) -> List[dict]:
        """列出所有记忆摘要"""
        results = []
        for f in sorted(self.memory_dir.glob("*_摘要.md")):
            try:
                stat = f.stat()
                results.append({
                    "name": f.stem,
                    "path": str(f),
                    "size": stat.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                })
            except Exception:
                pass
        return results

    def read_summary(self, task_name: str) -> Optional[str]:
        """读取指定任务的记忆摘要"""
        filepath = self.memory_dir / f"{task_name}_摘要.md"
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        # 尝试模糊匹配
        for f in self.memory_dir.glob(f"*{task_name}*_摘要.md"):
            return f.read_text(encoding="utf-8")
        return None

    def read_all_summaries(self) -> str:
        """读取所有记忆摘要（用于 AI 上下文）"""
        summaries = self.list_summaries()
        if not summaries:
            return "（暂无历史记忆）"
        lines = ["# 历史记忆\n"]
        for s in summaries:
            content = Path(s["path"]).read_text(encoding="utf-8")
            lines.append(f"\n## {s['name']}（{s['modified']}）\n{content[:1000]}")
        return "\n".join(lines)


    # ----------------------------------------------------------
    # 保存和检索记忆（key-value 存储）
    # ----------------------------------------------------------
    def _load_entries(self) -> dict:
        """加载所有记忆条目"""
        entries_file = self.memory_dir / "entries.json"
        if entries_file.exists():
            try:
                return json.loads(entries_file.read_text(encoding="utf-8"))
            except:
                return {}
        return {}

    def _save_entries(self, entries: dict):
        """保存所有记忆条目"""
        entries_file = self.memory_dir / "entries.json"
        entries_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, content: str, tags: Optional[List[str]] = None, title: str = "") -> str:
        """保存单条记忆，返回 ID"""
        if not title:
            title = content[:50]
        # 使用时间戳+随机后缀确保唯一性
        import random
        entry_id = f"mem_{int(time.time())}_{random.randint(1000, 9999)}"
        entries = self._load_entries()
        entries[entry_id] = {
            "id": entry_id,
            "title": title,
            "content": content,
            "tags": tags or [],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_entries(entries)
        logger.info(f"记忆已保存: {entry_id}")
        return entry_id

    def search(self, query: str, limit: int = 10) -> List[dict]:
        """搜索记忆，返回匹配条目"""
        entries = self._load_entries()
        query_lower = query.lower()
        results = []
        for entry_id, entry in entries.items():
            score = 0
            content_lower = entry.get("content", "").lower()
            tags = entry.get("tags", [])
            if query_lower in content_lower:
                score += content_lower.count(query_lower) * 10
            if any(query_lower in t.lower() for t in tags):
                score += 20
            if any(query_lower in w.lower() for w in content_lower.split()[:50]):
                score += 1
            if score > 0:
                results.append((score, entry))
        results.sort(key=lambda x: -x[0])
        return [{"id": r["id"], "content": r["content"], "title": r["title"], "tags": r["tags"], "score": s} for s, r in results[:limit]]

    def summarize(self, period: str = "today") -> List[dict]:
        """按周期生成摘要"""
        entries = self._load_entries()
        filtered = []
        for entry_id, entry in entries.items():
            saved = entry.get("saved_at", "")
            if period == "today" and saved[:10] == time.strftime("%Y-%m-%d"):
                filtered.append(entry)
            elif period == "all":
                filtered.append(entry)
        return filtered

    def list(self, limit: int = 20) -> List[dict]:
        """列出记忆条目"""
        entries = self._load_entries()
        result = sorted(entries.values(), key=lambda x: x.get("saved_at", ""), reverse=True)[:limit]
        return result

    def recall(self, query: str, limit: int = 5) -> List[dict]:
        """召回相关记忆（search 的别名）"""
        return self.search(query, limit=limit)


import logging
logger = logging.getLogger("memory")
