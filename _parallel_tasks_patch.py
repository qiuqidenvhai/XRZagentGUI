# -*- coding: utf-8 -*-
"""_parallel_tasks_patch.py —— 多任务真并发（每个任务一个独立浏览器标签页）。

════════════════════════════════════════════════════════════════════════
用户诉求（原话，2026-09-21）
════════════════════════════════════════════════════════════════════════
  「子母代理不是可以在一个浏览器里开两个标签吗，那多任务当然可以呀，
    而且我还可以不同任务用不同的AI平台，
    怎么会出现上一个任务正在执行需要排队的离谱信息」

用户说得对。旧的 commander.run() 用一把全局 asyncio.Lock 把所有任务串成队列，
抢不到锁就弹「上一条任务还在执行，你的新指令已排队」。那不是保护，那是偷懒。

════════════════════════════════════════════════════════════════════════
为什么原来必须加锁（不能直接删掉了事）
════════════════════════════════════════════════════════════════════════
后端 terminal.pyc 在 launch_agent 里创建了【唯一一份】模块级全局：
    browser_mgr  → 一个 BrowserManager（一个 Chromium 持久化上下文）
    session      → 一个 DeepSeekSession（一份对话上下文）
    commander    → 一个 Commander（一份 self._messages / 轮数计数）
所有 /command 都复用这同一套对象 → 同一张网页、同一个输入框。
此时两条指令并发，会真的互相踩踏：
  · 两条消息在同一个输入框里交替发出 → 模型收到串台上下文；
  · 两个 wait_response 抢同一页面的「最后一条消息」→ 回复张冠李戴；
  · 换新会话/轮数计数被两次调用互相触发 → 无限空转、任务卡死。
所以旧锁的本意没错，错的是【粒度】：它保护了「整个进程」，而真正需要保护的
只是「同一个 page」。

════════════════════════════════════════════════════════════════════════
本补丁的做法：按任务派生独立标签页（tab），锁收窄到 page 粒度
════════════════════════════════════════════════════════════════════════
BrowserManager.spawn_child() / PlatformBrowserManager.spawn_child() 本来就已经实现
了「在同一个浏览器上下文里开一张新 page（新标签页），共享 cookies/登录态」——
这正是用户说的「一个浏览器里开两个标签」。它原本只给子代理用，现在让【并发任务】
也走同一条路：

  任务到来时：
    · 若共享 page 空闲  → 直接用共享 page 跑（常见情况，零额外开销）；
    · 若共享 page 忙    → 立刻 spawn_child() 派生一张新标签页，
                          用这张新 page 跑，互不阻塞、各自等各自的回复。

这样：
  ✓ 多任务真并行（各占一张标签页）
  ✓ 不再出现「排队」提示
  ✓ 同一张标签页被两条指令打到时仍有 page 级锁兜底（不会串台）
  ✓ 不同任务用不同 AI 平台天然并存（不同平台本来就是不同浏览器进程）

安装方式与其它热补丁一致：terminal.py exec pyc 后由后台线程挂上，见 install()。
"""
import asyncio
import threading
import time

_INSTALLED = False
_LOCK = threading.Lock()

# 记录每个任务派生出来的 (child_bm, child_page)，任务结束回收，避免标签页泄漏
_SPAWNED: list = []


def _page_busy(page) -> bool:
    """判断某张 page 是否正被任务占用。

    判据：Commander 在跑时会把它挂到 _XRZ_BUSY_PAGES 里（见 _patched_run）。
    这里做成集合查询，避免依赖 page 的内部状态。
    """
    try:
        return id(page) in _BUSY_PAGES
    except Exception:
        return False


_BUSY_PAGES: set = set()


def _clone_session_for_task(old_session, child_bm):
    """为并发任务克隆一份【独立的会话对象】。

    ════════════════════════════════════════════════════════════════════
    为什么必须克隆会话，而不是只换 page（2026-09-21 真机翻车记录）
    ════════════════════════════════════════════════════════════════════
    第一版补丁只做了「把 commander._bm 换成子标签页」，session 仍是同一个对象。
    真机实测两条并发指令（token PA62373 / PB62373）的结果是：

        conv_deepseek_20260921_114626.json
            user      : ...PA62373
            user      : ...PB62373
            assistant : PB62373      ← 任务 A 拿到了任务 B 的答案！
            assistant : PB62373

    两条任务挤进同一个会话文件、且互相抢走对方的回复。原因很清楚：
    DeepSeekSession 内部持有 _messages（对话上下文）、_conv_file_path（落盘路径）
    和 _session_id。两个任务共用这一个对象时：
      · 两条用户消息先后 append 进同一个 _messages →
        _build_context_for_send() 把它们拼成一条 prompt 发给【同一个网页对话】；
      · 网页那一侧只有一个对话上下文，模型看到两条几乎相同的指令，只回其中一条
        （实测回的是后到的那条 PB62373），另一条任务的 wait_response 于是捞到了
        不属于它的回复 —— 这就是「张冠李戴」；
      · 两者还写同一个 _conv_file_path → 任务归属彻底混乱。

    正确做法：并发任务必须有【自己的会话上下文 + 自己的落盘文件】，
    就像它的标签页是独立的一样。这个函数用浅拷贝 + 显式重置做到这一点：
      · 拷贝类属性（系统提示词、平台、事件回调等配置性字段）；
      · _messages 重置为「仅系统提示词」——新任务从干净上下文开始，
        这与用户对「新建对话 = 新上下文」的既有要求一致；
      · _conv_file_path / _session_id 清空，让它为自己的任务新建落盘文件；
      · _bm 指向子标签页。
    """
    import copy as _copy
    # 【2026-09-25 真机修复·防错配】会话类型必须与子标签页浏览器类型匹配，
    # 绝不允许硬凑出「PlatformSession + DeepSeek BrowserManager」这种组合
    # （实测崩溃：BrowserManager.send_message 不收 attachments → TypeError，
    #  错误处理路径再访问 .profile → AttributeError，整个任务 1s 内被误杀）。
    # PlatformSession 只配 PlatformBrowserManager（带 .profile）；
    # DeepSeekSession 只配 BrowserManager（不带 .profile）。不匹配返回 None，
    # 由调用方退回「排队等共享 page 空闲」的安全路径。
    try:
        _old_is_platform = (type(old_session).__name__ == "PlatformSession")
        _child_is_platform = hasattr(child_bm, "profile")
        if _old_is_platform != _child_is_platform:
            import logging as _log
            _log.getLogger(__name__).warning(
                "[ParallelPatch] 会话/浏览器类型不匹配（session=%s, child_bm=%s），"
                "拒绝克隆，退回共享 page 排队", type(old_session).__name__,
                type(child_bm).__name__)
            return None
    except Exception:
        pass
    try:
        new_sess = _copy.copy(old_session)      # 浅拷贝：不重跑 __init__ 的副作用
    except Exception:
        return old_session
    try:
        # 全新对话上下文：只保留系统提示词（工具协议必须保留，否则模型不知道能调工具）
        sys_prompt = ""
        for m in (getattr(old_session, "_messages", None) or []):
            if getattr(m, "role", "") == "system":
                sys_prompt = getattr(m, "content", "") or ""
                break
        try:
            from agent_core.session import Message
            new_sess._messages = [Message(role="system", content=sys_prompt)] if sys_prompt else []
        except Exception:
            new_sess._messages = []
        # 新任务 = 新落盘文件、新会话 id、新动作日志
        new_sess._conv_file_path = ""
        new_sess._session_id = ""
        new_sess._action_log = []
        new_sess._restored_from = ""
        new_sess._bm = child_bm
        return new_sess
    except Exception:
        return old_session


def _make_patched_run(orig_run):
    """把 Commander.run 换成「按 page 分桶 + 忙则派生新标签页（含独立会话）」的版本。

    逻辑严格保持向后兼容：
      · 拿不到 browser_manager / page 时 → 原样调用 orig_run（不改变任何行为）；
      · 共享 page 空闲 → 直接 orig_run（零额外开销，行为与修复前完全一致）；
      · 共享 page 忙   → 派生新标签页 + 克隆独立会话，本任务在两者之上独立跑。
    """

    async def _patched_run(self, user_instruction, file_path=None, context_hints=""):
        bm = getattr(self, "_bm", None)
        page = getattr(bm, "_page", None) if bm is not None else None

        # 兜底：拿不到 page 身份就完全按原逻辑走（绝不因为补丁本身引入新风险）
        if bm is None or page is None:
            return await orig_run(self, user_instruction, file_path, context_hints)

        # ── 共享 page 空闲：直接用，行为和以前完全一样 ──
        if not _page_busy(page):
            _BUSY_PAGES.add(id(page))
            try:
                return await orig_run(self, user_instruction, file_path, context_hints)
            finally:
                _BUSY_PAGES.discard(id(page))

        # ── 共享 page 忙：派生一张新标签页 + 一份独立会话，本任务独立跑 ──
        # 这正是「一个浏览器里开两个标签」，也让多任务真正并行且不串台。
        child_bm = None
        new_session = None
        old_bm = self._bm
        old_session = getattr(self, "_session", None)
        try:
            spawn = getattr(bm, "spawn_child", None)
            if spawn is None:
                # 该后端不支持派生子窗口 → 退回「等共享 page 空出来」
                return await orig_run(self, user_instruction, file_path, context_hints)

            child_bm = await spawn()
            child_page = getattr(child_bm, "_page", None)
            if child_page is None:
                return await orig_run(self, user_instruction, file_path, context_hints)

            _BUSY_PAGES.add(id(child_page))
            _SPAWNED.append((time.time(), child_bm))

            # ★ 关键：换 page 的同时换【独立会话】，否则两个任务会挤进同一个
            #   _messages / 同一个落盘文件、并互相抢走对方的回复（真机已复现）。
            self._bm = child_bm
            if old_session is not None:
                new_session = _clone_session_for_task(old_session, child_bm)
                if new_session is None:
                    # 【2026-09-25 真机修复】会话与子标签页类型不匹配（如 commander 的
                    # _bm/_session 已被并发期间的平台切换弄成错配组合）→ 绝不硬跑。
                    # 关掉子标签页，排队等共享 page 空闲后再原逻辑执行。
                    import asyncio as _aio
                    try:
                        await child_bm.close()
                    except Exception:
                        pass
                    _BUSY_PAGES.discard(id(child_page))
                    _SPAWNED[:] = [x for x in _SPAWNED if x[1] is not child_bm]
                    child_bm = None
                    self._bm = old_bm
                    import logging as _log2
                    _log2.getLogger(__name__).warning(
                        "[ParallelPatch] 共享 page 忙且无法安全克隆会话 → 排队等待空闲")
                    while _page_busy(page):
                        await _aio.sleep(2)
                    _BUSY_PAGES.add(id(page))
                    try:
                        return await orig_run(self, user_instruction, file_path, context_hints)
                    finally:
                        _BUSY_PAGES.discard(id(page))
                self._session = new_session
                try:
                    new_session.set_on_event(getattr(old_session, "_on_event", None))
                except Exception:
                    pass

            try:
                return await orig_run(self, user_instruction, file_path, context_hints)
            finally:
                _BUSY_PAGES.discard(id(child_page))
        finally:
            # 【2026-09-25 真机修复】只还原【本任务自己设置】的对象。
            # 旧版无条件 self._bm = old_bm / self._session = old_session：
            # 若本任务运行期间用户切换了平台（commander._bm/_session 已被换成
            # 新平台的对象），收尾时会把新平台的配置踩回旧平台的——实测造成
            # 「_bm=DeepSeek BrowserManager + _session=通义 PlatformSession」错配，
            # 下一个任务一发送就 AttributeError 崩溃。按身份比较，别人换过就不动。
            try:
                if child_bm is not None and getattr(self, "_bm", None) is child_bm:
                    self._bm = old_bm
                if new_session is not None and getattr(self, "_session", None) is new_session:
                    self._session = old_session
            except Exception:
                pass
            if child_bm is not None:
                try:
                    await child_bm.close()
                except Exception:
                    pass

    _patched_run._xrz_parallel_patched = True
    _patched_run.__name__ = "run"
    try:
        _patched_run.__doc__ = orig_run.__doc__
    except Exception:
        pass
    return _patched_run


def _patch_commander_class(cls) -> bool:
    """给 Commander 类挂上并发补丁。返回是否已生效。"""
    if cls is None:
        return False
    run = cls.__dict__.get("run") or getattr(cls, "run", None)
    if run is None:
        return False
    if getattr(run, "_xrz_parallel_patched", False):
        return True
    try:
        cls.run = _make_patched_run(run)
        print("[XRZ-Parallel] 已为 Commander.run 安装「多任务并行（每任务一标签页）」补丁",
              flush=True)
        return True
    except Exception as e:
        print("[XRZ-Parallel] 安装 Commander.run 补丁失败:", e, flush=True)
        return False


def _do_patch_once(main_module, app_dir) -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            from agent_core.commander import Commander
        except Exception:
            return False
        if not _patch_commander_class(Commander):
            return False
        _INSTALLED = True
        return True


def install(main_module, app_dir, timeout_s=180):
    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if _do_patch_once(main_module, app_dir):
                    return
            except Exception as e:
                print("[XRZ-Parallel] 安装线程异常:", e, flush=True)
            time.sleep(0.2)
        print("[XRZ-Parallel] 安装超时，补丁未生效", flush=True)

    t = threading.Thread(target=_worker, daemon=True, name="xrz-parallel-tasks-patch")
    t.start()
    return t
