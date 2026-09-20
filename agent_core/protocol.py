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
        if not positions:
            # 连开头 @@@@ 都没有 → 协议违规，报错让 AI 纠正，不自动兜底。
            return None
        start = positions[0] + len(CMD_BEGIN)
        _brace_note = ""
        if len(positions) >= 2:
            end = positions[1]
            raw = text[start:end].strip()
        else:
            # 【实测】元宝会把回复截断，收尾 @@@@ 丢失，但开头 @@@@ 和完整 JSON 都在
            # （尾部到 "id":"1"} 为止）。这种情况不算协议违规，把平衡的 {...} 截出来，
            # 补一个「收尾 @@@@ 缺失」的修复说明，绝不能把好好的工具指令整个丢掉。
            # 先补花括号，再做平衡截取：否则 _extract_balanced_json 会命中
            # "params":{ 这个内层花括号，截出 {"path":...} 这种残缺对象。
            raw, _brace_note = self._ensure_object_braces(text[start:].strip())
            sub = self._extract_balanced_json(raw)
            if not sub:
                return None
            raw = sub
        if not raw:
            return None

        if not _brace_note:
            raw, _brace_note = self._ensure_object_braces(raw)

        obj, fixed, note = self._parse_json_robust(raw)
        if _brace_note:
            note = (note + " + " if note else "") + _brace_note
        if obj is not None and len(positions) < 2:
            note = (note + " + " if note else "") + "补全缺失的收尾@@@@（平台把回复截断了）"
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

    @staticmethod
    def _extract_balanced_json(s: str) -> str:
        """从文本里截取第一个括号平衡的 {...}（正确处理字符串与转义）。
        用于收尾 @@@@ 被平台截断时的兜底提取。"""
        for i, ch in enumerate(s):
            if ch != "{":
                continue
            depth = 0
            in_str = False
            esc = False
            for j in range(i, len(s)):
                c = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return s[i:j + 1]
            break
        return ""

    @staticmethod
    def _ensure_object_braces(raw: str):
        """把「丢了最外层花括号的对象体」补回花括号。

        实测（2026-09-13 DeepSeek 生成 pptx）：模型输出
            "tool":"pptx_create","params":{"path":"..."},"id":"1"
        少了首尾的 { }。不补的话整条工具指令会被判为协议违规而丢弃。
        只做最小干预：仅当文本不是以 { / [ 开头、且看起来是 "key": ... 序列时才补。
        """
        s = (raw or "").strip()
        if not s or s[0] in "{[":
            return s, ""
        if not re.match(r'^["\']?(tool|type)["\']?\s*:', s):
            return s, ""
        note = ""
        t = s.rstrip().rstrip(";").rstrip().rstrip(",")
        if not t.startswith("{"):
            t = "{" + t
            note = "补全缺失的最外层花括号"
        if not t.endswith("}"):
            t = t + "}"
            note = (note + " + " if note else "") + "补全缺失的收尾花括号"
        return t, note

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

        # 2.6) 同上，但只缺【收尾】花括号：@@@@{"tool":"x","params":{...},"id":"1"@@@@
        _t = (cleaned or raw or "").strip()
        if _t.startswith("{") and '"tool"' in _t and _t.count("{") > _t.count("}"):
            cand = _t + "}" * (_t.count("{") - _t.count("}"))
            try:
                obj = json.loads(cand)
                if isinstance(obj, dict) and obj.get("tool"):
                    note = (note + " + " if note else "") + "补全缺失的收尾花括号"
                    return obj, cand, note
            except Exception:
                pass

        # 3) 自动修复常见语法错误
        fixed, fix_note = self._try_fix(cleaned)

        # 【实测缺陷 2026-09-13 DeepSeek 生成 pptx】模型写成
        #   @@@@"tool":"pptx_create","params":{"path":"..."},"id":"1"@@@@
        # —— 最外层的 { } 整个漏了。旧逻辑先经过 _clean_json_text
        # （它会从第一个 '{' 截起，正好落在 "params":{ 上），
        # 解析结果变成 {"path":...} —— 没有 tool 字段 → 整条工具指令被丢弃，
        # 上层于是反复「纠正」，纠正 20 轮 × 每轮 20~40s → 任务超时、PPT 永远出不来。
        # 这里在最外层补一对花括号再试；只在补完确实能解析出带 tool 的对象时才采用。
        _s = (cleaned or "").strip()
        if _s and _s[0] not in "{[" and '"tool"' in _s:
            for cand in ("{" + _s + "}", "{" + _s):
                try:
                    obj = json.loads(cand)
                except Exception:
                    continue
                if isinstance(obj, dict) and obj.get("tool"):
                    note = (note + " + " if note else "") + "补全缺失的最外层花括号"
                    return obj, cand, note
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

        # 5) 最后兜底：以 { 开头但收尾花括号没配平（实测模型会漏写最后的 } ）
        _s2 = (cleaned or fixed or "").strip()
        if _s2.startswith("{") and '"tool"' in _s2:
            gap = _s2.count("{") - _s2.count("}")
            if gap > 0:
                cand = _s2 + "}" * gap
                try:
                    obj = json.loads(cand)
                    if isinstance(obj, dict) and obj.get("tool"):
                        note = (note + " + " if note else "") + "补全缺失的收尾花括号"
                        return obj, cand, note
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
