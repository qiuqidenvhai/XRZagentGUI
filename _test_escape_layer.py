"""验证 run_forever 自愈层：异常【直接逃逸】（未经工具注册表）时事件循环是否保住。"""
import asyncio, sys, threading, time

def run(with_patch: bool):
    if with_patch:
        orig = asyncio.BaseEventLoop.run_forever
        def resilient(self):
            n = 0
            while True:
                try:
                    orig(self); return
                except BaseException:
                    if sys.is_finalizing(): raise
                    n += 1
                    if self.is_closed() or n >= 20: return
                    continue
        asyncio.BaseEventLoop.run_forever = resilient

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    def th():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()
    threading.Thread(target=th, daemon=True).start()
    ready.wait(5)

    async def escaping():
        raise SystemExit("从协程里直接逃逸")

    fut = asyncio.run_coroutine_threadsafe(escaping(), loop)
    try:
        fut.result(timeout=10); desc = "任务返回（未逃逸）"
    except BaseException as e:
        desc = f"任务抛 {type(e).__name__}"

    time.sleep(1.0)
    alive = loop.is_running()

    second = "未执行"
    if alive:
        async def ping():
            return "pong"
        try:
            second = asyncio.run_coroutine_threadsafe(
                ping(), loop).result(timeout=10)
        except BaseException as e:
            second = f"!! {type(e).__name__}: {e}"

    try: loop.call_soon_threadsafe(loop.stop)
    except Exception: pass
    return desc, alive, second

for patch in (False, True):
    d, a, s = run(patch)
    tag = "有自愈补丁" if patch else "无自愈补丁（修复前）"
    print(f"[{tag}] 逃逸任务={d} | 循环存活={a} | 后续任务={s}")
    print(f"    → {'✅ 后端存活' if a and s == 'pong' else '❌ 后端被打死'}")
