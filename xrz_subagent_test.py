"""
xrz_subagent_test.py — 子代理系统直接端到端验证（不依赖 HTTP 后端）

为什么这样测：
- 子代理核心在 agent_core/subagent_manager.py：spawn_subagent 在【母代理进程内、
  同一浏览器开新窗口】跑一个子 Commander，最后把结果写 result.json，母代理用
  check_task/wait_task 收。这条链路没法靠 GUI 像素验证，但能靠直接驱动模块验证。
- 这里不绕道 /command（避免「agent 是否愿意调 browser_research」的不确定性），直接：
    1) 起一个无头 BrowserManager（复用 DeepSeek 持久化登录态）
    2) 注入给 SubAgentManager
    3) spawn_subagent(写文件任务)
    4) wait_task 等结果
    5) 检查 result.json 是否真写出来、findings 是否非空

判定：
- 成功：task.status==done 且 result.success 且 result.json 文件存在
- 失败：打印 task.result.error / 异常（这就是「子代理坏在哪」的证据）
"""
import asyncio
import sys
import json
import traceback
from pathlib import Path

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

from agent_core.browser import BrowserManager
from agent_core.subagent_manager import get_subagent_manager
from agent_core.xrz_paths import SUBAGENT_TASKS_DIR


async def main():
    print("=" * 60)
    print("[子代理测试] 启动母浏览器（无头，复用 DeepSeek 登录态）...")
    bm = BrowserManager(headless=True)
    try:
        await bm.launch()
    except Exception as e:
        print(f"[子代理测试][FATAL] 母浏览器启动失败: {e}")
        traceback.print_exc()
        return

    print("[子代理测试] 导航到 DeepSeek 并检查登录态...")
    try:
        await bm.navigate()
        logged_in = await bm.check_login()
    except Exception as e:
        print(f"[子代理测试][FATAL] 导航/登录检查异常: {e}")
        traceback.print_exc()
        await bm.close()
        return

    print(f"[子代理测试] 母浏览器登录态: {'已登录' if logged_in else '未登录'}")
    if not logged_in:
        print("[子代理测试][FAIL] 母浏览器未登录 → 子代理无法继承登录态（环境/凭据问题）")
        await bm.close()
        return

    # 注入母浏览器给子代理管理器
    sam = get_subagent_manager(str(SUBAGENT_TASKS_DIR))
    sam.set_browser_manager(bm)

    query = ("请用 file_write 工具创建一个文件，路径为 "
             "C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop\\test\\subagent_out.txt，"
             "内容为：子代理自测成功_仙人掌。完成后调用 done 汇报。")
    print(f"[子代理测试] 派发子代理任务:\n  {query}\n")

    try:
        tid = await sam.spawn_subagent(query, task_type="research")
    except Exception as e:
        print(f"[子代理测试][FAIL] spawn_subagent 抛异常: {e}")
        traceback.print_exc()
        await bm.close()
        return

    print(f"[子代理测试] 已派发 task_id={tid}，等待完成（最多 200s）...")
    try:
        task = await sam.wait_task(tid, timeout=200.0)
    except Exception as e:
        print(f"[子代理测试][FAIL] wait_task 抛异常: {e}")
        traceback.print_exc()
        await bm.close()
        return

    print("\n" + "=" * 60)
    print("[子代理测试] 结果：")
    print(f"  task_id     : {tid}")
    print(f"  status      : {task.status.value if task.status else 'None'}")
    if task.result:
        print(f"  success     : {task.result.success}")
        print(f"  error       : {task.result.error}")
        print(f"  output      : {(task.result.output or '')[:200]}")
        print(f"  findings    : {(task.result.findings or '')[:200]}")
        print(f"  files       : {[f.get('name') for f in (task.result.files or [])]}")
    else:
        print("  result      : None（无结果对象）")

    rp = Path(task.result_path)
    print(f"  result.json : exists={rp.exists()} path={rp}")
    if rp.exists():
        try:
            raw = json.loads(rp.read_text(encoding="utf-8"))
            print(f"  result.json : success={raw.get('success')} files={len(raw.get('files', []))}")
        except Exception as e:
            print(f"  result.json : 解析失败 {e}")

    # 产物文件检查
    out = Path(r"C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test\subagent_out.txt")
    if out.exists():
        print(f"  产物文件    : 存在 -> {out.read_text(encoding='utf-8', errors='ignore')[:80]!r}")
    else:
        print("  产物文件    : 不存在（子代理未真正写文件）")

    ok = (task.status.value == "done" and task.result and task.result.success and rp.exists())
    print("\n" + "=" * 60)
    print(f"[子代理测试] 最终判定: {'PASS' if ok else 'FAIL'}")
    print("=" * 60)

    await bm.close()


if __name__ == "__main__":
    asyncio.run(main())
