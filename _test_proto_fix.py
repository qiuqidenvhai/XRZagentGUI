# -*- coding: utf-8 -*-
"""协议解析容错单测：模型漏写最外层花括号时也要能救回工具指令。"""
import sys
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.protocol import Protocol

p = Protocol()
for t in ("pptx_create", "file_write", "done"):
    p.register_tool(t, {})

cases = [
    # 实测 2026-09-13 DeepSeek：漏了最外层 {}
    '@@@@"tool":"pptx_create","params":{"path":"D:\\软件\\a.pptx","title":"x"},"id":"1"@@@@',
    # 正常
    '@@@@\n{"tool":"file_write","params":{"path":"a.txt","content":"hi"},"id":"1"}\n@@@@',
    # 漏了结尾 }
    '@@@@{"tool":"file_write","params":{"path":"a.txt"},"id":"1"@@@@',
    # 漏了结尾 @@@@（平台截断）
    '@@@@{"tool":"done","params":{},"id":"9"}',
    # 纯自然语言（不该解析出工具）
    '我只是随便说句话，没有协议',
]
for c in cases:
    r = p.extract_all(c)
    if r:
        x = r[0]
        print("OK  ", repr(c[:50]), "-> tool=", x.command.tool,
              "params=", x.command.params, "fix=", x.fix_note)
    else:
        print("NONE", repr(c[:50]))
