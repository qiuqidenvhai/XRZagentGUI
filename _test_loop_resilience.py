"""
验证「工具抛 SystemExit 会打死事件循环」这个缺陷已被修复。

模拟后端真实结构：事件循环在子线程里 run_forever()，HTTP 线程用
run_coroutine_threadsafe 往里投任务（和 terminal.pyc 的 _post_command_to_loop 一样）。

用例：
  A. 工具抛 SystemExit → 任务应降级为 error，事件循环必须仍然存活
  B. 上一条跑完后，再投一条正常任务 → 必须还能执行（证明后端没被打死）
  C. 对照：不套自愈补丁时，同样场景事件循环会死（用来证明这缺陷是真的）
"""
import asyncio
import sys
import threading
import time

sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from agent_core.commander import ToolRegistry  # noqa: E402


class FakeCommander:
    pass


def make_registry():
    reg = ToolRegistry(FakeCommander())

    async def boom(**params):
        # 模拟「环境级删除拦截」：抛 SystemExit（BaseException，不是 Exception）
        raise SystemExit("safe-delete: recycle bin unavailable")

    async def ok_tool(**params):
        return "工具正常返回"

    reg.register("boom", "会抛 SystemExit 的工具", boom)
    reg.register("ok_tool", "正常工具", ok_tool)
    return reg


def run_case(with_patch: bool):
    """在子线程里起事件循环，投一个会抛 SystemExit 的工具任务，再看循环是否存活。"""
    if with_patch:
        # 复刻 terminal.py 里的自愈补丁
        orig = asyncio.BaseEventLoop.run_forever

        def resilient(self):
            n = 0
            while True:
                try:
                    orig(self)
                    return
                except BaseException:
                    if sys.is_finalizing():
                        raise
                    n += 1
                    if self.is_closed() or n >= 20:
                        return
                    continue

        asyncio.BaseEventLoop.run_forever = resilient

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def loop_thread():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    t = threading.Thread(target=loop_thread, daemon=True)
    t.start()
    ready.wait(5)

    reg = make_registry()

    # 1) 投一个会抛 SystemExit 的任务
    fut = asyncio.run_coroutine_threadsafe(reg.execute("boom", {}), loop)
    try:
        r1 = fut.result(timeout=15)
        # 注意：ToolRegistry 已兜住，这里应正常返回 error
        r1_desc = f"status={r1.status} error={getattr(r1, 'error', '')[:60]!r}"
    except BaseException as e:
        r1_desc = f"!! 任务本身炸了: {type(e).__name__}: {e}"

    time.sleep(1.0)
    loop_alive_1 = loop.is_running()

    # 2) 再投一条正常任务：后端还活着就必须能执行
    r2_desc = "未执行"
    if loop_alive_1:
        try:
            fut2 = asyncio.run_coroutine_threadsafe(reg.execute("ok_tool", {}), loop)
            r2 = fut2.result(timeout=15)
            r2_desc = f"status={r2.status} output={getattr(r2, 'output', '')!r}"
        except BaseException as e:
            r2_desc = f"!! 第二条也炸了: {type(e).__name__}: {e}"

    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass
    return r1_desc, loop_alive_1, r2_desc


print("=" * 72)
print("用例 1：不加自愈补丁（模拟修复前）")
r1, alive1, r2 = run_case(with_patch=False)
print(f"  抛 SystemExit 的工具任务 → {r1}")
print(f"  之后事件循环是否仍存活 → {alive1}")
print(f"  再投一条正常任务       → {r2}")
print(f"  结论：{'❌ 后端被打死（缺陷复现）' if not alive1 else '✅ 后端存活'}")

print("=" * 72)
print("用例 2：套上自愈补丁 + 修复后的 ToolRegistry")
r1, alive2, r2 = run_case(with_patch=True)
print(f"  抛 SystemExit 的工具任务 → {r1}")
print(f"  之后事件循环是否仍存活 → {alive2}")
print(f"  再投一条正常任务       → {r2}")
ok = alive2 and "status=success" in r2 and "status=error" in r1
print(f"  结论：{'✅ 单工具失败不再拖垮后端' if ok else '❌ 仍有问题'}")
print("=" * 72)
