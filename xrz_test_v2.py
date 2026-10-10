"""
重跑测试（修复版）- 绕过旧缓存问题
"""
import asyncio
import json
import sys
import time
from pathlib import Path

# 【2026-10-06】项目目录也动态化：装在 D 盘 / C 盘 / 任意路径都能跑
PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
# 【2026-10-06 修"写死桌面位置"】原来写死开发者桌面路径，换台电脑就跑到
# 别人桌面上去、还可能目录不存在。改为按本机真实桌面解析。
try:
    from agent_core.user_paths import desktop_dir as _up_desktop
    TEST_ROOT = Path(_up_desktop(create=True)) / "test"
except Exception:
    import os
    TEST_ROOT = Path(os.path.expanduser("~")) / "Desktop" / "test"

import requests

def switch_platform(key):
    r = requests.post("http://127.0.0.1:8888/switch_platform", json={"platform": key}, timeout=10)
    return r.json()

def post(cmd):
    r = requests.post("http://127.0.0.1:8888/command", json={"command": cmd}, timeout=10)
    return r.json()

def run_task(name, platform, command, check_fn):
    print(f"\n=== {name} | {platform} ===")
    switch_platform(platform)
    result = post(command)
    print(f"  结果: {result.get('text', '')[:200]}")

    # 简单等待最终回复
    time.sleep(5)

    ok, extra = check_fn()
    print(f"  >>> {'PASS' if ok else 'FAIL'} {extra}")
    return ok, result.get('text', ''), result.get('output', ''), result, extra

def main():
    overall = []

    # T1 DeepSeek Word
    # 【2026-10-06】提示词里的路径用本机真实测试目录，不写死任何用户名
    _t1_path = str(TEST_ROOT / "report.docx").replace("\\", "\\\\")
    ok, _, _, _, _ = run_task(
        "T1_DeepSeek_Word", "deepseek",
        f"请生成一份Word报告保存到{_t1_path}，标题《测试报告》",
        lambda: (True, "report.docx exists") if (TEST_ROOT / "report.docx").exists() else (False, "report.docx missing")
    )
    overall.append(("T1 DeepSeek Word", ok))

    # T3 Qwen file_edit
    note = TEST_ROOT / "note.txt"
    note.write_text("初始内容：仙人掌自测。\n", encoding="utf-8")

    ok, _, _, _, extra = run_task(
        "T3_Qwen_FileEdit", "tongyi",
        f"请用file_edit工具在{str(note).replace(chr(92), chr(92) * 2)}末尾追加：TEST_OK_仙人掌自测",
        lambda: ("TEST_OK_仙人掌自测" in note.read_text(encoding="utf-8", errors="replace"), "note.txt 含 TEST_OK")
    )
    overall.append(("T3 Qwen file_edit", ok))

    # 汇总
    npass = sum(1 for _, ok in overall if ok)
    print(f"\n=== 总裁判: {npass}/{len(overall)} ===")
    for name, ok in overall:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

if __name__ == "__main__":
    main()
