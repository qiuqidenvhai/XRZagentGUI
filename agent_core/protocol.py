"""
protocol.py — 仙人掌 Agent JSON 指令协议解析器

设计目标：
- 容忍 AI 产出的各种「不规范 JSON」（脚本自动修复，不靠 AI 重答）
- 自动剥离代码围栏 / BOM / 注释 / 前后散文
- 兼容单引号、未加引号 key、Python 字面量、尾随逗号等常见错误
"""
import re
import json
from typing import Optional, List, Tuple
from dataclasses import dataclass
from types import SimpleNamespace

CMD_BEGIN = "@@@@"
CMD_END = "@@@@"
RAW_BEGIN = "<<<RAW>>>"
RAW_END = "<<<RAW>>>"


@dataclass
class ParsedCommand:
    raw: str
    command: SimpleNamespace
    id: str = ""
    fixed: bool = False
    fix_note: str = ""


@dataclass
class ExecutionResult:
    id: str
    status: str
    tool: str
    output: str = ""
    error: str = ""


class Protocol:
    def __init__(self):
        self._tools: dict = {}

    def register_tool(self, name: str, spec: dict):
        self._tools[name] = spec

    # ---- 核心解析 ----
    def extract(self, text: str) -> Optional[ParsedCommand]:
        """提取工具调用指令，支持标准协议、RAW命令、自动JSON修复"""
        # 1. 先尝试 RAW 格式
        raw_cmd = self._extract_raw(text)
        if raw_cmd:
            return raw_cmd
        
        # 2. 尝试标准 @@@@...@@@@ 双层协议
        block = self._extract_json(text)
        if block:
            return block
        
        # 3. 检测协议违规：单层 @ 或无 @
        at_count = text.count('@')
        if at_count == 1:
            # 单层 @ → 协议违规，返回 None 让上层纠正
            pass
        elif at_count == 0:
            # 完全无 @ → 协议违规
            pass
        # 多个 @ 但未成对 → 可能是 AI 乱发，返回 None
        
        return None

    def _extract_raw(self, text: str) -> Optional[ParsedCommand]:
        positions = [m.start() for m in re.finditer(re.escape(RAW_BEGIN), text)]
        if len(positions) < 2:
            return None
        start = positions[0] + len(RAW_BEGIN)
        end = positions[1]
        command = text[start:end].strip()
        if not command:
            return None
        return ParsedCommand(
            raw=RAW_BEGIN + command + RAW_END,
            command=SimpleNamespace(tool="raw_shell", params={"command": command}, id=""),
        )

    def _extract_json(self, text: str) -> Optional[ParsedCommand]:
        """
        仅从第一个 @@@@ ... @@@@ 块里抽取并解析。
        【硬性协议要求】命令必须用 @@@@ 包裹。漏写 @@@@ 视为协议违规，
        直接返回 None，交由上层发送纠正（要求 AI 用 @@@@ 包裹命令），
        绝不从裸文本自动抓取 JSON（否则协议约束形同虚设）。
        """
        positions = [m.start() for m in re.finditer(re.escape(CMD_BEGIN), text)]
        if len(positions) < 2:
            # 没有成对的 @@@@ → 协议违规，报错让 AI 纠正，不自动兜底。
            return None
        start = positions[0] + len(CMD_BEGIN)
        end = positions[1]
        raw = text[start:end].strip()
        if not raw:
            return None

        obj, fixed, note = self._parse_json_robust(raw)
        if obj is None:
            return None

        # 兜底：确认有 tool 字段，否则不算有效指令
        tool = obj.get("tool", obj.get("type", ""))
        if not tool:
            return None

        return ParsedCommand(
            raw=CMD_BEGIN + (fixed if fixed is not None else raw) + CMD_END,
            command=SimpleNamespace(
                tool=tool,
                params=obj.get("params", {}) or {},
                id=obj.get("id", ""),
            ),
            fixed=fixed is not None,
            fix_note=note or "",
        )

    # ---- 鲁棒解析（主入口）----
    def _parse_json_robust(self, raw: str) -> Tuple[Optional[dict], Optional[str], str]:
        """
        尽力解析 AI 产出的（常常不规范的）JSON。
        返回 (obj, fixed_text, note)：
          - obj 为 None 表示彻底失败
          - fixed_text 为修复后的文本（成功且与原文不同则非 None）
          - note 为修复说明（用于向用户展示「已自动修复」）
        """
        note = ""

        # 1) 原样解析
        try:
            return json.loads(raw), None, ""
        except Exception:
            pass

        # 2) 清理：去代码围栏 / BOM / 注释 / 前后散文
        cleaned = self._clean_json_text(raw)
        if cleaned != raw:
            note = "清理代码围栏/注释/前后散文"
            try:
                return json.loads(cleaned), cleaned, note
            except Exception:
                pass

        # 3) 自动修复常见语法错误
        fixed, fix_note = self._try_fix(cleaned)
        if fix_note:
            note = (note + " + " if note else "") + fix_note
        try:
            return json.loads(fixed), fixed, note
        except Exception:
            pass

        # 4) 退一步：从原文里抓第一个配对 {…} 或 […] 再修
        sub = self._extract_json_substring(raw)
        if sub and sub != cleaned:
            fixed2, fix_note2 = self._try_fix(sub)
            try:
                obj = json.loads(fixed2)
                extra = "提取JSON子串" + ((" + " + fix_note2) if fix_note2 else "")
                note = (note + " + " if note else "") + extra
                return obj, fixed2, note
            except Exception:
                pass

        return None, None, note

    @staticmethod
    def _clean_json_text(s: str) -> str:
        """去掉代码围栏、BOM、零宽字符、注释、前后散文，返回尽量干净的 JSON 文本。"""
        # 去 BOM / 零宽字符
        for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\u00a0"):
            s = s.replace(ch, "")
        # 去 ```json / ``` / ~~~ 围栏
        s = re.sub(r"```[a-zA-Z]*", "", s)
        s = s.replace("```", "")
        s = re.sub(r"~~~[a-zA-Z]*", "", s)
        s = s.replace("~~~", "")
        # 去 /* */ 块注释
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
        # 去 // 行注释（避免误伤 http:// 中的 //：仅当 : 不在前面时）
        s = re.sub(r'(?<![:/\w])//[^\n]*', '', s)
        s = s.strip()
        # 去掉最外层配对之外的前导语（第一个 { 或 [ 之前的内容）
        m = re.search(r"[\{\[]", s)
        if m and m.start() > 0:
            s = s[m.start():]
        # 去掉尾部散文（最后一个 } 或 ] 之后的内容）
        m2 = re.search(r"[\}\]](?!.*[\}\]])", s, flags=re.DOTALL)
        if m2 and m2.end() < len(s):
            s = s[:m2.end()]
        return s.strip()

    @staticmethod
    def _extract_json_substring(s: str) -> str:
        """抓取第一个配对的 {…} 或 […] 子串（忽略字符串内的括号）。"""
        m = re.search(r"[\{\[]", s)
        if not m:
            return s
        depth = 0
        in_str = False
        esc = False
        start = m.start()
        open_ch = s[start]
        close_ch = "}" if open_ch == "{" else "]"
        for i in range(start, len(s)):
            c = s[i]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
        return s[start:]

    # ---- 自动修复（脚本处理，不靠 AI）----
    def _try_fix(self, raw: str) -> Tuple[str, str]:
        """
        修复常见 AI JSON 语法错误，返回 (修复后文本, 修复说明)。
        顺序：单引号 → 未引号key → Python字面量 → 尾逗号 → 重复逗号 → 反斜杠转义。
        """
        notes = []
        s = raw

        # 1. 单引号 → 双引号（仅修复「结构边界包围的 '...'」，避免误伤英文缩写）
        if "'" in s:
            s2 = re.sub(r"(?<=[\{\[\(,:\s])'([^']*)'(?=[\}\],:\s])", r'"\1"', s)
            if s2 != s:
                notes.append("单引号→双引号")
                s = s2

        # 2. 未加引号的 key：{ name: ... } / { "a":1, age: 2 }
        if re.search(r"([{,]\s*)[A-Za-z_][A-Za-z0-9_]*\s*:", s):
            s = re.sub(
                r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)",
                lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
                s,
            )
            notes.append("未加引号key")

        # 3. Python 字面量 True/False/None
        s2 = re.sub(r"\bTrue\b", "true", s)
        s2 = re.sub(r"\bFalse\b", "false", s2)
        s2 = re.sub(r"\bNone\b", "null", s2)
        if s2 != s:
            notes.append("Python字面量")
            s = s2

        # 4. 尾随逗号
        if re.search(r",\s*([}\]])", s):
            s = re.sub(r",\s*([}\]])", r"\1", s)
            notes.append("尾随逗号")

        # 5. 重复逗号 ,, → ,
        if re.search(r",\s*,", s):
            s = re.sub(r",\s*,", ",", s)
            notes.append("重复逗号")

        # 6. 反斜杠转义：把单独 \ 转义为 \\（保留 \\ \n \t \r \u \" \/ \b \f）
        try:
            json.loads(s)
            return s, " + ".join(notes) if notes else ""
        except Exception:
            pass

        def fix_bs(t: str) -> str:
            out = []
            i = 0
            while i < len(t):
                if t[i] == "\\":
                    if i + 1 < len(t) and t[i + 1] in ('"', "\\", "n", "t", "r", "u", "/", "b", "f"):
                        out.append(t[i:i + 2])
                        i += 2
                        continue
                    out.append("\\\\")
                    i += 1
                    continue
                out.append(t[i])
                i += 1
            return "".join(out)

        s = fix_bs(s)
        notes.append("反斜杠转义")
        return s, " + ".join(notes) if notes else ""

    def extract_all(self, text: str) -> List[ParsedCommand]:
        """只返回第一个有效指令（每次一个）"""
        raw_cmd = self._extract_raw(text)
        if raw_cmd:
            return [raw_cmd]
        block = self._extract_json(text)
        return [block] if block else []

    def validate(self, block: ParsedCommand) -> tuple:
        tool = block.command.tool
        if not tool:
            return False, "缺少 tool 字段"
        if tool not in self._tools and tool != "test":
            return False, f"未注册工具: {tool}"
        return True, ""

    def wrap_result(self, result: ExecutionResult, fix_note: str = "") -> str:
        obj = {
            "id": result.id,
            "status": result.status,
            "tool": result.tool,
        }
        if result.output:
            obj["output"] = result.output
        if result.error:
            obj["error"] = result.error
        if fix_note:
            obj["_note"] = fix_note
        json_str = json.dumps(obj, ensure_ascii=False)
        return f"{CMD_BEGIN}\n{json_str}\n{CMD_END}"

    def describe_fix(self, block: ParsedCommand) -> str:
        """生成「已自动修复」的展示文本，供 GUI / 日志使用。"""
        if not block.fixed or not block.fix_note:
            return ""
        return f"[自动修复] 已修正 AI 的 JSON 语法错误（{block.fix_note}）"


def wrap_message(msg_type: str, content: dict, msg_id: str = "") -> str:
    obj = {"type": msg_type, **content, "id": msg_id}
    return f"{CMD_BEGIN}\n{json.dumps(obj, ensure_ascii=False)}\n{CMD_END}"
