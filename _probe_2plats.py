#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两平台验证：豆包 / 元宝 —— 验证修复后能否拿到真实回复且不再 raw_shell 死循环。"""
import sys, json, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import _xrz_harness as H

TARGETS = sys.argv[1:] or ["doubao", "yuanbao"]

print("=== DeepSeek 实例数（应为 1 或 0，绝不允许翻倍）===", H.deepseek_count())
for k in TARGETS:
    print(f"\n########## 平台 {k} ##########")
    print("切换:", H.switch_platform(k))
    print("健康:", H.health())

    r1 = H.run("你好，请用一句话介绍你自己", timeout=200)
    print(f"[闲聊] ok={r1['ok']} task={r1['task_id']} tools={sorted(r1['tools'])} errs={r1['errs'][:2]}")
    print(f"       回复: {r1['final'][:180]!r}")

    r2 = H.run("请用 file_write 工具创建文件 fixchk_" + k + ".txt，内容为「" + k + " 工具测试成功」", timeout=260)
    print(f"[工具] ok={r2['ok']} task={r2['task_id']} tools={sorted(r2['tools'])} errs={r2['errs'][:2]}")
    print(f"       回复: {r2['final'][:180]!r}")
    fp = Path("xrz_data/XianRenZhang_tasks/fixchk_" + k + ".txt")
    if not fp.exists():
        fp = Path("fixchk_" + k + ".txt")
    print(f"       文件存在: {fp.exists()} -> {fp}")

    print("DeepSeek 实例数:", H.deepseek_count())

print("\n=== 最终上下文目录 ===")
for d, c in sorted(H.all_context_dirs().items()):
    print(" ", c, d)
