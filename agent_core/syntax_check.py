"""生成文件的语法校验器。

用途：Agent 用 file_write / file_edit 生成代码后立即自检，
把语法错误直接回报给 AI，让它当轮就修好，而不是等运行才发现。

设计原则：
- 只做「静态语法」检查，不执行任何用户代码（安全）。
- 校验失败不回滚写入（内容可能仍在编辑中），但返回醒目的错误提示。
- 外部工具（node）不可用时静默跳过，绝不因校验本身报错而打断任务。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple

# 需要做语法校验的扩展名
CHECKABLE = {".py", ".js", ".mjs", ".cjs", ".json", ".html", ".htm", ".css"}

# node --check 的超时（秒）
_NODE_TIMEOUT = 10


def _check_python(content: str, path: Path) -> Tuple[bool, str]:
    import ast
    try:
        ast.parse(content)
        return True, ""
    except SyntaxError as e:
        loc = f"第 {e.lineno} 行" if e.lineno else "未知位置"
        col = f"，第 {e.offset} 列" if e.offset else ""
        return False, f"Python 语法错误（{loc}{col}）: {e.msg}\n  >>> {(e.text or '').rstrip()}"


def _check_json(content: str, path: Path) -> Tuple[bool, str]:
    try:
        json.loads(content)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSON 语法错误（第 {e.lineno} 行，第 {e.colno} 列）: {e.msg}"


def _check_js(content: str, path: Path) -> Tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return True, ""  # 无 node，跳过
    try:
        # --check 只解析不执行，安全
        r = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True, text=True, timeout=_NODE_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or "").strip()
        # 精简 node 的冗长输出，只留关键几行
        lines = [ln for ln in err.splitlines() if ln.strip()][:6]
        return False, "JavaScript 语法错误:\n  " + "\n  ".join(lines)
    except subprocess.TimeoutExpired:
        return True, ""
    except Exception:
        return True, ""  # 校验本身出错不影响主流程


def _check_html(content: str, path: Path) -> Tuple[bool, str]:
    """轻量 HTML 检查：标签配对（不依赖外部库）。"""
    import re
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}
    stack = []
    # 去掉注释和 script/style 内容，避免误判
    cleaned = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    cleaned = re.sub(r"<(script|style)\b.*?</\1>", "", cleaned, flags=re.S | re.I)
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>", cleaned):
        closing, tag, self_close = m.group(1), m.group(2).lower(), m.group(3)
        if tag in void or self_close:
            continue
        if closing:
            if not stack:
                return False, f"HTML 标签不匹配: 多余的结束标签 </{tag}>"
            if stack[-1] != tag:
                return False, f"HTML 标签不匹配: 期望 </{stack[-1]}>，实际 </{tag}>"
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        return False, "HTML 标签未闭合: " + ", ".join(f"<{t}>" for t in stack[-5:])
    return True, ""


def _check_css(content: str, path: Path) -> Tuple[bool, str]:
    """轻量 CSS 检查：大括号配对。"""
    depth = 0
    for ch in content:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False, "CSS 大括号不匹配: 出现多余的 '}'"
    if depth != 0:
        return False, f"CSS 大括号未闭合: 缺少 {depth} 个 '}}'"
    return True, ""


_CHECKS = {
    ".py": _check_python,
    ".json": _check_json,
    ".js": _check_js,
    ".mjs": _check_js,
    ".cjs": _check_js,
    ".html": _check_html,
    ".htm": _check_html,
    ".css": _check_css,
}


def check_file(path, content: str | None = None) -> Tuple[bool, str]:
    """校验文件语法。返回 (是否通过, 错误描述)。

    path: 文件路径（决定用哪种校验器）
    content: 文件内容；为 None 时从磁盘读取
    """
    path = Path(path)
    ext = path.suffix.lower()
    fn = _CHECKS.get(ext)
    if fn is None:
        return True, ""  # 不支持的类型，跳过

    if content is None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return True, ""
    try:
        return fn(content, path)
    except Exception:
        # 校验器自身异常绝不能影响主流程
        return True, ""


def format_result(path, ok: bool, msg: str) -> str:
    """把校验结果格式化成给 AI 看的提示文本。"""
    if ok:
        return ""
    return (
        f"\n⚠️ 语法自检未通过（{Path(path).name}），请立即修正后重新写入：\n{msg}\n"
        f"提示：修正后再次调用 file_write 覆盖该文件即可。"
    )
