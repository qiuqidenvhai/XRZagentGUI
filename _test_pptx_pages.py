# -*- coding: utf-8 -*-
"""离线验证 pptx 页数：用户要 N 页，模板构建后到底有几页。"""
import sys, json, pathlib
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core import pptx_builder as pb

CASES = {
    "3页纯标题（回归用例）": [
        {"heading": "综合测试", "bullets": []},
        {"heading": "功能展示", "bullets": []},
        {"heading": "谢谢", "bullets": []},
    ],
    "3页带正文": [
        {"heading": "综合测试", "bullets": ["要点A", "要点B"]},
        {"heading": "功能展示", "bullets": ["要点C", "要点D"]},
        {"heading": "谢谢", "bullets": []},
    ],
    "5页纯标题": [
        {"heading": f"第{i}页标题", "bullets": []} for i in range(1, 6)
    ],
}

for name, slides in CASES.items():
    try:
        spec = pb.build_edits_json("minimal-business-summary", slides, "演示文稿", "")
        sel = spec["selected_slides"]
        print(f"{name}: 要求 {len(slides)} 页 → 实际 {len(sel)} 页  {sel}")
    except Exception as e:
        print(f"{name}: ERROR {type(e).__name__}: {e}")
