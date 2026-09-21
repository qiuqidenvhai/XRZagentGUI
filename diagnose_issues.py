"""
仙人掌Agent问题诊断报告
"""
import json
from pathlib import Path
import subprocess
import urllib.request

# 配置
API = "http://127.0.0.1:8888"
PROJECT_DIR = Path("D:/软件/XianRenZhangAgent")

print("="*60)
print("仙人掌Agent问题诊断报告")
print("="*60)

# 1. 检查后端状态
print("\n[1] 后端健康检查")
try:
    with urllib.request.urlopen(f"{API}/health", timeout=3) as resp:
        data = json.loads(resp.read())
        print(f"  ✓ 状态: {data.get('status', '?')}")
        print(f"  ✓ Agent就绪: {data.get('agent_ready', '?')}")
        print(f"  ✓ 平台: {data.get('platform', '?')}")
except Exception as e:
    print(f"  ✗ 后端连接失败: {e}")

# 2. 检查任务列表
print("\n[2] 任务列表")
try:
    with urllib.request.urlopen(f"{API}/conversations", timeout=5) as resp:
        data = json.loads(resp.read())
        tasks = data.get('tasks', [])
        print(f"  ✓ 总任务数: {len(tasks)}")
        print(f"  最近任务:")
        for t in tasks[:5]:
            print(f"    - {t['id'][:25]} | {t.get('platform','?'):10} | {t.get('title','?')[:40]}")
except Exception as e:
    print(f"  ✗ 获取任务列表失败: {e}")

# 3. 检查附件列表
print("\n[3] 附件列表")
try:
    with urllib.request.urlopen(f"{API}/attachments", timeout=5) as resp:
        data = json.loads(resp.read())
        files = data.get('files', [])
        print(f"  ✓ 总附件数: {len(files)}")
        # 按类型分组
        types = {}
        for f in files:
            ext = Path(f['name']).suffix.lower()
            types[ext or '(无扩展名)'] = types.get(ext or '(无扩展名)', 0) + 1
        for ext, count in sorted(types.items()):
            print(f"    - {ext}: {count}个")
except Exception as e:
    print(f"  ✗ 获取附件列表失败: {e}")

# 4. 检查中断功能
print("\n[4] 中断功能")
try:
    req = urllib.request.Request(f"{API}/interrupt", method='POST')
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
        print(f"  ✓ 中断端点正常: {data.get('type', '?')}")
except Exception as e:
    print(f"  ✗ 中断功能异常: {e}")

# 5. GUI文件检查
print("\n[5] GUI文件检查")
gui_files = [
    PROJECT_DIR / "test_screenshots/gui.html",
    PROJECT_DIR / "gui.html",
]

for gui in gui_files:
    if gui.exists():
        print(f"  ✓ 找到GUI: {gui}")
        content = gui.read_text(encoding='utf-8')

        # 检查关键函数
        checks = {
            'showThinking': 'function showThinking' in content,
            'appendThinking': 'function appendThinking' in content,
            'endThinking': 'function endThinking' in content,
            'pendingAttachments': 'let pendingAttachments' in content,
            'eventSource': 'new EventSource' in content,
            'loadHistory': 'function loadHistory' in content or 'async function loadHistory' in content,
            'deleteTask': 'function deleteTask' in content or 'async function deleteTask' in content,
            'interruptTask': 'function interruptTask' in content or 'async function interruptTask' in content,
        }

        print("  关键功能检查:")
        for name, exists in checks.items():
            print(f"    {'✓' if exists else '✗'} {name}")

        # 检查变量名一致性
        if 'attachForThis' in content:
            print("  ⚠ 发现错误的变量名: attachForThis (应为 pendingAttachments)")
        else:
            print("  ✓ 变量名一致: 使用 pendingAttachments")
        break
else:
    print("  ✗ 未找到GUI文件")

# 6. 后端源代码检查
print("\n[6] 后端源代码状态")
source_files = [
    PROJECT_DIR / "terminal.py",
    PROJECT_DIR / "desktop_app.py",
    PROJECT_DIR / "agent_core/commander.py",
    PROJECT_DIR / "agent_core/session.py",
    PROJECT_DIR / "agent_core/pptx_builder.py",
]

for f in source_files:
    exists = f.exists()
    print(f"  {'✓' if exists else '✗'} {f.name}")

# 7. 数据目录状态
print("\n[7] 数据目录状态")
data_dirs = [
    PROJECT_DIR / "xrz_data" / "gui_attachments",
    PROJECT_DIR / "xrz_data" / "conversations",
    PROJECT_DIR / "xrz_data" / ".xianrenzhang_agent" / "tasks.json",
]

for d in data_dirs:
    if d.exists():
        if d.is_dir():
            count = len(list(d.iterdir()))
            print(f"  ✓ {d.relative_to(PROJECT_DIR)}: {count}个文件")
        else:
            size = d.stat().st_size
            print(f"  ✓ {d.relative_to(PROJECT_DIR)}: {size/1024:.1f}KB")
    else:
        print(f"  ✗ {d.relative_to(PROJECT_DIR)}: 不存在")

# 8. 问题总结
print("\n" + "="*60)
print("问题总结")
print("="*60)

issues = []

# 检查GUI变量名
gui_path = PROJECT_DIR / "test_screenshots/gui.html"
if gui_path.exists():
    content = gui_path.read_text(encoding='utf-8')
    if 'attachForThis' in content:
        issues.append(("严重", "GUI变量名错误: attachForThis 应为 pendingAttachments"))

# 检查思考过程显示
if gui_path.exists():
    content = gui_path.read_text(encoding='utf-8')
    if 'function showThinking' not in content:
        issues.append(("严重", "缺少showThinking函数，思考过程无法显示"))
    if 'function appendThinking' not in content:
        issues.append(("严重", "缺少appendThinking函数，Agent思考过程无法显示"))

# 检查后端源代码
if not (PROJECT_DIR / "terminal.py").exists():
    issues.append(("严重", "缺少terminal.py（主入口文件）"))

# 检查删除功能
if gui_path.exists():
    content = gui_path.read_text(encoding='utf-8')
    if 'deleteTask' not in content:
        issues.append(("中等", "缺少deleteTask函数"))

# 检查历史任务加载
if gui_path.exists():
    content = gui_path.read_text(encoding='utf-8')
    if 'loadHistory' not in content:
        issues.append(("中等", "缺少loadHistory函数"))

if not issues:
    print("✓ 未发现严重问题")
else:
    print(f"\n发现 {len(issues)} 个问题:")
    for severity, desc in issues:
        print(f"  [{severity}] {desc}")

print("\n" + "="*60)
