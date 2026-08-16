"""
buffer_store.py — 文本缓冲区（文件备份的持久剪贴板）

用途：
- 当 AI 需要输入很长的文本时，可以分批次把内容写进缓冲区（每个缓冲区是一个 txt 文件）。
- 通过命令把一段字符串「塞进」缓冲区，再通过命令把缓冲区内容「取出来」输出，或写到另一个文件。
- 自带内存/磁盘管理：单缓冲上限、缓冲数量上限、按时间自动清理过期文件，避免垃圾文件堆积。

命令示例（在 GUI 命令框或 CLI 输入）：
  buffer write demo 你好世界          # 覆盖写入
  buffer append demo 第二段内容        # 追加（分批输入用这个）
  buffer get demo                     # 取出全部内容（输出到对话）
  buffer save demo out.txt            # 把内容写到另一个文件
  buffer load demo in.txt             # 把一个文件读进缓冲区
  buffer list                         # 列出所有缓冲区
  buffer clear demo                   # 删除某个缓冲区
  buffer clear                        # 清空所有缓冲区
  buffer cleanup                      # 立即执行自动清理（删过期+清垃圾）
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

# ─── 配置（内存/磁盘管理）───
from agent_core.xrz_paths import BUFFERS_DIR as DEFAULT_ROOT, TASKS_DIR
MAX_BUFFER_BYTES = 10 * 1024 * 1024     # 单缓冲上限 10MB
MAX_BUFFERS = 200                        # 缓冲数量上限
RETENTION_DAYS = 7                       # 过期清理阈值（天）
DISPLAY_LIMIT = 20000                    # 超过此长度，get 自动落盘并只预览

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class BufferError(Exception):
    pass


def _is_under(path: Path, root: Path) -> bool:
    """判断 path 是否位于 root 之内（防路径穿越）"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


class BufferStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        # 启动时轻量维护（只做数量上限，不删用户文件）
        self._maintenance()

    # ── 安全 ──
    def _safe_name(self, name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise BufferError("缓冲区名称不能为空")
        if not _NAME_RE.match(name):
            raise BufferError("缓冲区名称只允许字母/数字/_ . -，且不能包含路径分隔符")
        return name

    def _path(self, name: str) -> Path:
        return self.root / f"{self._safe_name(name)}.txt"

    # ── 写 ──
    def write(self, name: str, text: str, append: bool = False) -> dict:
        text = text or ""
        p = self._path(name)
        # 分批输入时直接拼接，不自动加分隔符，保证还原出的文本与原文一致
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(text)
        size = p.stat().st_size
        truncated = False
        # 内存管理：超过上限则截断并警告
        if size > MAX_BUFFER_BYTES:
            self._truncate(p, MAX_BUFFER_BYTES)
            size = MAX_BUFFER_BYTES
            truncated = True
        # 数量管理：超过上限删最旧
        self._cap_count()
        return {
            "name": name, "size": size, "append": append,
            "truncated": truncated, "path": str(p),
            "msg": f"已{'追加' if append else '写入'}缓冲区 [{name}]，大小 {size} 字节"
                   + ("（已截断到上限）" if truncated else ""),
        }

    def _truncate(self, p: Path, limit: int):
        # 字节级截断，避免多字节字符被切开；marker 用纯 ASCII 以精确控制上限
        marker = b"\n[TRUNCATED: exceeded single-buffer limit]\n"
        with open(p, "rb") as f:
            raw = f.read(max(0, limit - len(marker)))
        text = raw.decode("utf-8", errors="ignore")  # 丢弃被截断的多字节残片
        with open(p, "wb") as f:
            f.write(text.encode("utf-8") + marker)

    def _cap_count(self):
        files = self._list_files()
        if len(files) > MAX_BUFFERS:
            files.sort(key=lambda x: x.stat().st_mtime)  # 最旧在前
            for old in files[: len(files) - MAX_BUFFERS]:
                try:
                    old.unlink()
                except Exception:
                    pass

    # ── 读 ──
    def get(self, name: str) -> dict:
        p = self._path(name)
        if not p.exists():
            return {"found": False, "name": name, "content": "", "size": 0,
                    "msg": f"缓冲区 [{name}] 不存在"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"found": True, "name": name, "content": content,
                "size": len(content.encode("utf-8")), "path": str(p),
                "msg": f"已取出缓冲区 [{name}]，{len(content)} 字符"}

    def list(self) -> dict:
        files = self._list_files()
        items = []
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                sz = f.stat().st_size
                mt = int(f.stat().st_mtime)
            except Exception:
                sz, mt = 0, 0
            items.append({"name": f.stem, "size": sz, "mtime": mt})
        return {"items": items, "count": len(items),
                "msg": f"共 {len(items)} 个缓冲区"
                       + (": " + ", ".join(i["name"] for i in items) if items else "")}

    def save(self, name: str, out_path: str, allowed_roots: Optional[list] = None) -> dict:
        info = self.get(name)
        if not info["found"]:
            return {"ok": False, "msg": info["msg"]}
        out = Path(out_path).expanduser()
        # 安全：只允许写到任务目录或项目目录，避免误删/覆盖个人文件
        allowed = allowed_roots or [
            TASKS_DIR,
            Path.cwd(),
        ]
        if not any(_is_under(out, r) for r in allowed):
            return {"ok": False,
                    "msg": f"出于安全，save 只允许写到任务目录/项目目录内（{out} 不在允许范围）"}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(info["content"], encoding="utf-8")
        return {"ok": True, "name": name, "path": str(out),
                "size": len(info["content"].encode("utf-8")),
                "msg": f"缓冲区 [{name}] 已保存到 {out}"}

    def load(self, name: str, in_path: str) -> dict:
        src = Path(in_path).expanduser()
        if not src.exists():
            return {"ok": False, "msg": f"源文件不存在: {src}"}
        text = src.read_text(encoding="utf-8", errors="replace")
        return self.write(name, text, append=False)

    def clear(self, name: Optional[str] = None) -> dict:
        if name:
            p = self._path(name)
            if p.exists():
                p.unlink()
                return {"ok": True, "msg": f"已删除缓冲区 [{name}]"}
            return {"ok": True, "msg": f"缓冲区 [{name}] 不存在，无需删除"}
        files = self._list_files()
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass
        return {"ok": True, "msg": f"已清空全部缓冲区（{len(files)} 个）"}

    # ── 自动清理（内存/磁盘管理）──
    def cleanup(self, retention_days: int = RETENTION_DAYS) -> dict:
        removed = []
        now = time.time()
        # 1) 过期缓冲区
        for f in self._list_files():
            try:
                age = now - f.stat().st_mtime
                if age > retention_days * 86400:
                    f.unlink()
                    removed.append(f.name)
            except Exception:
                pass
        # 2) 数量上限（再保险）
        self._cap_count()
        # 3) 工作区垃圾文件清理
        removed += self._cleanup_workspace_junk(retention_days)
        return {"ok": True, "removed": removed, "count": len(removed),
                "msg": f"自动清理完成，移除 {len(removed)} 个文件"
                       + (": " + ", ".join(removed[:10]) if removed else "")}

    def _cleanup_workspace_junk(self, retention_days: int = RETENTION_DAYS) -> list:
        """清理任务目录下的已知垃圾文件（不影响缓冲区与用户产出）。"""
        removed = []
        roots = [TASKS_DIR]
        junk_patterns = ("*.tmp", "*.part", "*.autosave", "pending_*", "*.swp", "~*")
        now = time.time()
        for base in roots:
            if not base.exists():
                continue
            for pat in junk_patterns:
                for f in base.glob(pat):
                    try:
                        if f.is_file() and (now - f.stat().st_mtime) > retention_days * 86400:
                            f.unlink()
                    except Exception:
                        pass
        return removed

    def _maintenance(self):
        """启动时轻量维护：数量上限（不删用户文件）。"""
        try:
            self._cap_count()
        except Exception:
            pass

    def _list_files(self):
        try:
            return list(self.root.glob("*.txt"))
        except Exception:
            return []


_store: Optional[BufferStore] = None


def get_buffer_store(root: Optional[Path] = None) -> "BufferStore":
    global _store
    if _store is None:
        _store = BufferStore(root)
    return _store
