#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证修复后的 harness：task_id 关联 + 工具识别 + 不双开。"""
import sys, time, os
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, run, deepseek_count, switch_platform

print("health:", health(), flush=True)
print("deepseek 实例数(基线):", deepseek_count(), flush=True)

print("\n[切换 deepseek]", switch_platform("deepseek"), flush=True)
time.sleep(2)

r1 = run("请用一句话解释什么是 Python 装饰器（不要调用任何工具）", 180)
print("\n== chat ==\n ok=", r1["ok"], "\n task_id=", r1["task_id"], "\n tools=", sorted(r1["tools"]),
      "\n types=", dict(r1["types"]), "\n final=", r1["final"][:160], flush=True)

W = r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks"
before = set(os.listdir(W))
r2 = run("请用 docx_create 工具生成 Word 文档，path 设为 "
         r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\smoke_harness.docx，"
         "标题「Harness 验证」，正文「修复后 harness 工作正常」", 220)
after = set(os.listdir(W))
print("\n== docx ==", "\n ok=", r2["ok"], "\n task_id=", r2["task_id"], "\n tools=", sorted(r2["tools"]),
      "\n final=", r2["final"][:160], "\n 新文件=", sorted(after - before), flush=True)
fp = os.path.join(W, "smoke_harness.docx")
print(" smoke_harness.docx 存在=", os.path.exists(fp), os.path.getsize(fp) if os.path.exists(fp) else "", flush=True)

print("\ndeepseek 实例数(终):", deepseek_count(), flush=True)
