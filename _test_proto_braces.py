# -*- coding: utf-8 -*-
"""协议容错单测：模型漏写最外层花括号时，工具指令不能被整条丢弃。"""
import sys
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.protocol import Protocol

p = Protocol()
p.register_tool("pptx_create", {})
p.register_tool("done", {})

CASES = [
    ("漏写最外层花括号（实测 DeepSeek pptx）",
     '@@@@"tool":"pptx_create","params":{"path":"D:\\软件\\a.pptx","pages":[1,2]},"id":"1"@@@@'),
    ("正常写法",
     '@@@@\n{"tool":"pptx_create","params":{"path":"D:\\软件\\a.pptx"},"id":"1"}\n@@@@'),
    ("漏结尾 } ",
     '@@@@{"tool":"pptx_create","params":{"path":"a.pptx"},"id":"1"@@@@'),
    ("纯自然语言（不该解析出工具）",
     "我只是说句话，没有协议"),
]

for name, t in CASES:
    r = p.extract_all(t)
    if r:
        c = r[0].command
        print(f"[OK ] {name}: tool={c.tool} id={c.id} params_keys={list(c.params)} fix={r[0].fix_note}")
    else:
        print(f"[NONE] {name}")
