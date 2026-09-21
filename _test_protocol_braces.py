# -*- coding: utf-8 -*-
"""协议解析容错单测：模型漏写最外层花括号时，工具指令不能被整个丢弃。"""
import sys
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.protocol import Protocol

p = Protocol()
p.register_tool("pptx_create", {})
p.register_tool("done", {})

CASES = [
    # （说明，原文）
    ("漏写最外层花括号（实测 DeepSeek 生成 pptx 的样子）",
     '@@@@"tool":"pptx_create","params":{"path":"D:\\软件\\xrz_data\\a.pptx","title":"x"},"id":"1"@@@@'),
    ("正常写法",
     '@@@@\n{"tool":"pptx_create","params":{"path":"D:\\软件\\a.pptx"},"id":"1"}\n@@@@'),
    ("漏了收尾花括号",
     '@@@@{"tool":"pptx_create","params":{"path":"a.pptx"},"id":"1"@@@@'),
    ("纯自然语言（不应解析出工具）",
     "我只是随便说句话，没有协议"),
]

for desc, text in CASES:
    r = p.extract_all(text)
    if r:
        c = r[0].command
        print("[OK ] %s -> tool=%s params=%s fix=%r" % (desc, c.tool, c.params, r[0].fix_note))
    else:
        print("[   ] %s -> 未解析出指令" % desc)
