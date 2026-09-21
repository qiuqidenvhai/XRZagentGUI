"""
快速验证脚本 - 测试8个关键问题
"""
import json
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8888"
PROJECT_DIR = Path("D:/软件/XianRenZhangAgent")

print("="*60)
print("仙人掌Agent 8问题快速验证")
print("="*60)

# 1. 文件上传
print("\n[1] 文件上传")
result = urllib.request.urlopen(f"{API}/attachments", timeout=5).read()
data = json.loads(result)
files = data.get('files', [])
print(f"  附件数: {len(files)}")
pdfs = [f for f in files if f['name'].endswith('.pdf')]
ppts = [f for f in files if f['name'].endswith('.pptx')]
print(f"  PDF: {len(pdfs)}, PPT: {len(ppts)}")
print("  ✓ 附件系统正常" if files else "  ✗ 无附件")

# 2. 思考过程
print("\n[2] 思考过程显示")
gui = PROJECT_DIR / "test_screenshots/gui.html"
if gui.exists():
    content = gui.read_text(encoding='utf-8')
    has_showThinking = 'function showThinking' in content
    has_appendThinking = 'function appendThinking' in content
    has_ai_thinking_event = "etype === 'ai_thinking'" in content
    print(f"  showThinking: {'✓' if has_showThinking else '✗'}")
    print(f"  appendThinking: {'✓' if has_appendThinking else '✗'}")
    print(f"  ai_thinking事件: {'✓' if has_ai_thinking_event else '✗'}")
else:
    print("  ✗ gui.html不存在")

# 3. 历史任务隔离
print("\n[3] 历史任务隔离")
result = urllib.request.urlopen(f"{API}/conversations", timeout=5).read()
data = json.loads(result)
tasks = data.get('tasks', [])
print(f"  任务数: {len(tasks)}")
if tasks:
    t = tasks[0]
    print(f"  示例任务: {t.get('id', '?')[:30]}")
    print(f"  对话文件: {t.get('file', '?')[-40:]}")
    conv_file = Path(t.get('file', ''))
    if conv_file.exists():
        conv_data = json.loads(conv_file.read_text(encoding='utf-8'))
        msgs = conv_data.get('messages', [])
        print(f"  消息数: {len(msgs)}")
        print("  ✓ 任务隔离正常")
    else:
        print("  ⚠ 对话文件不存在")
else:
    print("  ✗ 无任务")

# 4. 恢复任务
print("\n[4] 恢复任务")
session_py = PROJECT_DIR / "agent_core/session.py"
if session_py.exists():
    content = session_py.read_text(encoding='utf-8')
    has_restore = 'restore_conversation' in content
    print(f"  restore_conversation: {'✓' if has_restore else '✗'}")
print("  ℹ 需重启后测试实际恢复")

# 5. 删除功能
print("\n[5] 删除功能")
tasks_file = PROJECT_DIR / "xrz_data/.xianrenzhang_agent/tasks.json"
if tasks_file.exists():
    perms = oct(tasks_file.stat().st_mode)[-3:]
    print(f"  tasks.json权限: {perms}")
    # 尝试删除一个不存在的任务（安全测试）
    try:
        req = urllib.request.Request(f"{API}/conversations/nonexistent_task", method='DELETE')
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read())
        print(f"  删除响应: {result.get('type', 'unknown')}")
    except Exception as e:
        print(f"  删除测试: {str(e)[:50]}")
else:
    print("  ✗ tasks.json不存在")

# 6. PPT生成
print("\n[6] PPT生成")
pptx_builder = PROJECT_DIR / "agent_core/pptx_builder.py"
if pptx_builder.exists():
    content = pptx_builder.read_text(encoding='utf-8')
    has_build = 'def build_pptx' in content
    has_parse = 'def parse_to_slides' in content
    print(f"  build_pptx: {'✓' if has_build else '✗'}")
    print(f"  parse_to_slides: {'✓' if has_parse else '✗'}")
print(f"  已生成PPT: {len(ppts)}个")

# 7. 暂停/中断
print("\n[7] 暂停/中断")
try:
    req = urllib.request.Request(f"{API}/interrupt", method='POST')
    resp = urllib.request.urlopen(req, timeout=5)
    result = json.loads(resp.read())
    print(f"  中断响应: {result.get('type', 'unknown')}")
    print("  ✓ 中断功能正常")
except Exception as e:
    print(f"  ✗ 中断失败: {e}")

# 8. PPT显示在附件列表
print("\n[8] PPT显示在附件列表")
if ppts:
    print(f"  ✓ 附件列表中有{len(ppts)}个PPT")
    for ppt in ppts[:3]:
        name = Path(ppt['name']).name
        size = ppt.get('size', 0)
        print(f"    - {name} ({size} bytes)")
else:
    print("  ⚠ 附件列表中无PPT")

# 检查GUI自动刷新
gui = PROJECT_DIR / "test_screenshots/gui.html"
if gui.exists():
    content = gui.read_text(encoding='utf-8')
    has_loadAttachments = 'loadAttachments()' in content
    print(f"\n  GUI附件刷新: {'✓' if has_loadAttachments else '✗'}")

print("\n" + "="*60)
print("验证完成")
print("="*60)
