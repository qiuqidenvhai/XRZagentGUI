# -*- coding: utf-8 -*-
"""协议容错单测：模型漏写最外层花括号时，工具指令不能被整条丢弃。"""
import sys
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.protocol import Protocol

p = Protocol()
p.register_tool("pptx_create", {})
cases = [
    # 实测 2026-09-13 DeepSeek：漏了最外层 {}
    '@@@@"tool":"pptx_create","params":{"path":"D:\\软件\\xrz_data\\a.pptx"},"id":"1"@@@@',
    # 正常写法
    '@@@@\n{"tool":"pptx_create","params":{"path":"a.pptx"},"id":"1"}\n@@@@',
    # 漏了收尾 @@
    '@@@@{"tool":"pptx_create","params":{"path":"a.pptx"},"id":"1"}',
    # 纯自然语言（不该解析出东西）
    '我只是随便说句话，没有协议',
]
for c in cases:
    r = p.extract_all(c)
    if r:
        x = r[0]
        print('OK   tool=%s params=%s fix=%s' % (x.command.tool, x.command.params, x.fix_note))
    else:
        print('NONE')
