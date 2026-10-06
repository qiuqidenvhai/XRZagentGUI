"""
browser.py — Playwright 浏览器管理器（支持多上下文/子代理共享登录态）

核心改进：
1. 持久化用户数据目录 → 一次登录，永久有效
2. 同一用户数据目录下开多个 context/page → 共享 cookies/token，无需重复登录
3. 每个子代理获得独立 context → 互不干扰，但都带着主代理的登录态
"""
import asyncio
import sys
import logging
import json
import os
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("browser")

DEEPSEEK_URL = "https://chat.deepseek.com"
CHAT_URL = "https://chat.deepseek.com/"

# 浏览器窗口图标（仙人掌浏览器版，替代默认 Chromium 图标）
BROWSER_ICON_PATH = Path(__file__).resolve().parent.parent / "__browser_cactus_icon.ico"

# 浏览器数据目录（用户数据，包含登录状态、cookies、扩展等）
# 全部落在 D 盘项目目录内，绝不写 C:\Users\...（见 agent_core/xrz_paths.py）
from agent_core.xrz_paths import (
    USER_DATA_DIR,
    NEW_BROWSER_DATA_ROOT,
    DEEPSEEK_DATA_DIR,
    COOKIE_FILE,
)

logger.setLevel(logging.DEBUG)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(h)


class BrowserManager:
    """
    浏览器管理器 — 持久化用户数据目录 + 多上下文模式

    用法：
        1. 主代理：launch() 创建持久化上下文（一次性登录）
        2. 子代理：BrowserManager() + launch_share_existing() → 共享登录态
    """
    _shared_browser_instance = None  # 全局单例 ChromiumBrowser
    _shared_launch_lock = asyncio.Lock()
    _launched = False

    def __init__(self, headless: bool = False, user_data_dir: str = None):
        self.headless = headless
        self._user_data_dir_override = user_data_dir or str(DEEPSEEK_DATA_DIR)
        self._browser = None
        self._page = None
        self._playwright = None
        self._chromium = None
        # 标记是否为「子窗口」（共享母代理浏览器，只关自己的 page，不关整个浏览器）
        self._is_child = False
        # 【同目录防双开】是否为「接管同目录 owner 的 context」：True = 与 owner 共享浏览器进程，
        # close() 只关自己的 page，绝不关 owner、绝不摘 owner 的 registry 登记。
        self._adopted = False

    @property
    def context(self):
        return self._browser

    @property
    def page(self):
        return self._page

    def _find_browser_pids(self):
        """找到本 context 对应 Chromium 主进程的 PID（用于僵尸检测）。

        主进程特征：cmdline 同时含「本 user_data_dir 目录名」与 --remote-debugging-pipe。
        找不到返回 None（僵尸检测退化为 close 监听 + is_closed，不影响主流程）。
        """
        import psutil
        try:
            marker = Path(self._user_data_dir_override).name.lower()
        except Exception:
            return None
        pids = set()
        try:
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cl = p.info.get("cmdline") or []
                    if "--remote-debugging-pipe" not in cl:
                        continue
                    full = " ".join(cl)
                    if "--user-data-dir=" + marker in full or marker in full:
                        pids.add(p.info["pid"])
                except Exception:
                    continue
        except Exception:
            return None
        return pids or None

    def _is_zombie(self):
        """同步判定 self._browser 是否僵尸（进程死但 Playwright 侧还挂着）。

        判死条件（任一命中）：
          1) close 监听回调已触发（Chromium 自杀时 Playwright 会发 context close）
          2) is_closed() 为 True
          3) 登记的 _chrome_pids 全部进程已退出
        """
        if self._browser is None:
            return False, "无 context"
        try:
            if getattr(self, "_ctx_closed", False):
                return True, "close 监听已触发"
            if hasattr(self._browser, "is_closed") and self._browser.is_closed():
                return True, "is_closed()"
            pids = getattr(self, "_chrome_pids", None)
            if pids:
                import psutil
                if not any(psutil.pid_exists(pid) for pid in pids):
                    return True, f"浏览器进程全部退出 {sorted(pids)}"
        except Exception:
            return True, "检测异常（保守判死）"
        return False, "存活"

    async def _clear_zombie(self):
        """僵尸则强制清理（同步判定 + 异步清理），让 launch() 走完整重建。"""
        if self._browser is None:
            return
        dead, reason = self._is_zombie()
        if not dead:
            return
        logger.warning(f"[BrowserManager] 检测到僵尸浏览器（{reason}），强制清理后重建...")
        self._close_context_and_cleanup()

    def _close_context_and_cleanup(self):
        """把死 context 的所有引用清零（供 navigate/launch 复用的清理路径）。"""
        self._ctx_closed = False
        try:
            if self._browser is not None:
                self._browser.remove_listener("close", self._on_context_closed)
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._chrome_pids = None

    def _on_context_closed(self):
        """context close 事件回调：标记已关，供 _is_zombie 快速判死。"""
        try:
            self._ctx_closed = True
            logger.warning("[BrowserManager] 浏览器上下文已关闭（close 监听），下次调用将自动重建")
        except Exception:
            pass

    async def launch(self):
        # 验证现有浏览器是否真活着——死引用不算「已启动」。
        # 【僵尸修复】Chromium 进程被杀/崩溃后，Playwright 侧 context 对象可能仍
        # is_closed()=False，旧代码永远"复用"这个死 context → new_page 报
        # Target has been closed → 「页面未初始化 / 消息发送失败」。
        # 现在：close 监听 + is_closed + PID 级存活验证，僵尸一律清理后走完整重建。
        if self._browser is not None:
            await self._clear_zombie()
            if self._browser is not None:
                logger.info("浏览器已启动，复用")
                return

        from playwright.async_api import async_playwright

        logger.info("准备启动持久化 Chromium 浏览器...")

        # 使用指定的用户数据目录或默认的
        user_data_dir = self._user_data_dir_override
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        # 【同目录防双开】同目录（同一平台 profile）已有活 owner 时直接接管复用，
        # 绝不重开第二个持久化上下文、绝不 kill owner 的活浏览器（否则掉登）。
        # 不同目录（跨平台）键不同，互不影响 → 可并存多浏览器。
        from agent_core import profile_lock as _pl
        _owner = _pl.acquire(user_data_dir, self)
        if _owner is not None and _owner is not self:
            logger.info(f"[BrowserManager] 同目录已有活 owner（{type(_owner).__name__}），"
                        f"接管复用其浏览器进程（同平台不双开，防掉登）")
            # context 绑定 owner 的 Playwright 连接，必须共享 owner 的实例，
            # 不能自己再 pw.start()（新连接用不了别人的 context）。
            self._playwright = _owner._playwright
            self._browser = _owner._browser
            self._chrome_pids = getattr(_owner, "_chrome_pids", None)
            self._adopted = True          # close() 只关自己的 page，绝不关 owner、绝不 stop pw
            self._ctx_closed = False
            try:
                self._browser.on("close", self._on_context_closed)
            except Exception:
                pass
            self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
            await self._load_cookies_from_file()
            logger.info("[BrowserManager] 已接管同目录 owner 的浏览器（跨实例共享，零双开）")
            return
        # 走到这里 self 就是本目录 owner：清跨进程孤儿残留 + 锁文件
        self._cleanup_lock_files(Path(user_data_dir))

        # 2. 启动 Playwright
        self._playwright = await async_playwright().start()
        logger.info("Playwright 已启动")

        # 3. 创建持久化上下文
        # 注：--no-sandbox / --disable-gpu / --disable-dev-shm-usage 在受限制/容器/沙箱
        # 环境里必需（否则 Chromium 渲染子进程易被沙箱策略杀掉，表现为交互时
        # “Target page has been closed”）。对普通桌面机器无害（仅降低隔离强度）。
        _launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-service-autorun",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
        ]
        # 【2026-09-21 代理故障自愈】Windows 的 WPAD/自动代理探测可能拿到一个
        # 已死的代理（ERR_PROXY_CONNECTION_FAILED，实测本机直连是通的）。
        # 一旦进程内检测到代理故障（类级标记），重启浏览器时改用直连。
        if getattr(BrowserManager, "_no_proxy_mode", False):
            _launch_args.append("--no-proxy-server")
            logger.warning("[BrowserManager] 检测到历史代理故障，本次以 --no-proxy-server 直连启动")
        try:
            # 尝试持久化上下文
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=_launch_args,
                accept_downloads=True,
            )
            logger.info(f"持久化上下文创建成功: {user_data_dir}")
            # 注册 close 监听 + 记录浏览器 PID（僵尸检测用）
            try:
                self._browser.on("close", self._on_context_closed)
            except Exception:
                pass
            self._chrome_pids = self._find_browser_pids()
            BrowserManager._shared_browser_instance = self
        except Exception as e:
            _msg = str(e)
            # 关键修复：profile 已被其他实例占用（SingletonLock / 单实例冲突）时，
            # 绝对不要偷偷开一个「未登录的临时浏览器」——那正是用户看到的
            # 「两个 DeepSeek 浏览器（一个没登录）」的根因。直接抛出明确错误。
            if any(k in _msg for k in ("SingletonLock", "already in use", "single instance", "locked", "被占用")):
                _pl.release(user_data_dir, self)
                raise RuntimeError(
                    f"DeepSeek profile（{user_data_dir}）已被其他浏览器实例占用，"
                    f"请勿同时打开两个 DeepSeek 浏览器。请先关闭多余的实例后再试。"
                )
            logger.warning(f"持久化上下文创建失败（非锁冲突）: {e}，尝试临时目录兜底")
            tmp_dir = tempfile.mkdtemp(prefix="xrz_chrome_")
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=_launch_args,
                accept_downloads=True,
            )
            logger.warning(f"[BrowserManager] 已退化为临时隔离上下文（未登录态）: {tmp_dir}")

        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        # 4. 加载 cookies
        await self._migrate_cookies_to_new_dir()
        await self._load_cookies_from_file()

        # 5. 立即启动图标设置轮询（后台任务，窗口出现后自动设置仙人球图标）
        try:
            asyncio.get_event_loop().create_task(self._icon_poll_loop())
        except Exception:
            pass

        logger.info("浏览器启动完成")
        self._launched = True

    async def _icon_poll_loop(self):
        """后台轮询设置仙人球图标。

        【2026-09-21 修复】原来「一旦成功就 break」，导致图标只贴到 launch 后
        最先冒出来的临时窗口（如「要恢复页面吗？」）上，晚几秒才出现的主窗口
        永远没被设置 → 用户仍看到 Chromium 原图标。
        现在持续补 ~30s，不做成功即停。
        """
        try:
            for _ in range(30):
                await asyncio.sleep(1)
                await self._apply_window_icon_once()
        except Exception:
            pass

    async def _apply_window_icon_once(self):
        """尝试设置一次图标，返回是否成功"""
        try:
            if sys.platform != "win32" or self.headless:
                return False
            if not BROWSER_ICON_PATH.exists():
                return False

            import ctypes
            from ctypes import wintypes

            ico = BROWSER_ICON_PATH
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1

            def make_icon(size):
                return user32.LoadImageW(None, str(ico), IMAGE_ICON, size, size, LR_LOADFROMFILE)

            hicon_big = make_icon(256)
            hicon_sm = make_icon(32)
            if not hicon_big and not hicon_sm:
                return False

            def is_playwright_chromium(pid: int) -> bool:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not h:
                    return False
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        return False
                    p = buf.value.lower().replace("/", "\\")
                    return (("playwright_browsers" in p) or ("ms-playwright" in p)) and p.endswith("chrome.exe")
                finally:
                    kernel32.CloseHandle(h)

            hits = []
            EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def cb(hwnd, lparam):
                try:
                    if user32.IsWindowVisible(hwnd):
                        cls = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, cls, 256)
                        if cls.value == "Chrome_WidgetWin_1":
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            if pid.value and is_playwright_chromium(pid.value):
                                hits.append(hwnd)
                except Exception:
                    pass
                return True

            user32.EnumWindows(EnumProc(cb), 0)

            if not hits:
                return False

            # 【2026-09-21 修复】必须给**所有**窗口都设置，不能碰到第一个就 return。
            # Chromium 会陆续创建多个顶层窗口，只设第一个会把图标贴错对象。
            ok = 0
            for hwnd in hits:
                try:
                    user32.SendMessageTimeoutW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm, 0x0002, 1000, None)
                    user32.SendMessageTimeoutW(hwnd, WM_SETICON, ICON_BIG, hicon_big, 0x0002, 1000, None)
                    ok += 1
                    logger.debug(f"图标设置成功 HWND={hwnd}")
                except Exception:
                    pass
            return ok > 0
        except Exception as e:
            logger.debug(f"_apply_window_icon_once 异常: {e}")
            return False

    async def spawn_child(self, headless: bool = None):
        """在同一浏览器实例里开一个新窗口（新 page），返回共享登录态的子管理器。

        关键设计（修复子代理登录态丢失 / SingletonLock 冲突）：
        - 子管理器与母代理**共用同一个浏览器进程**和同一个持久化上下文
          （self._browser 是 launch_persistent_context 返回的 BrowserContext），
          因此 cookies / localStorage / 登录态 100% 共享，无需复制任何 profile。
        - 子管理器只拥有自己的一个新 page（相当于新标签页 / 新窗口），
          与母代理的 page 相互独立、互不干扰。
        - 不再复制目录、不再清理 SingletonLock、不再触发「第二个实例」冲突。
        """
        if self._browser is None:
            raise RuntimeError("母代理浏览器尚未启动，无法派生子窗口")

        child = BrowserManager(
            headless=self.headless if headless is None else headless,
            user_data_dir=self._user_data_dir_override,
        )
        # 与母代理共享底层资源（同一个浏览器进程 / 同一套登录态）
        child._playwright = self._playwright
        child._chromium = self._chromium
        child._browser = self._browser          # 共享同一持久化上下文（= 同一浏览器）
        child._is_child = True                  # 标记为子窗口：close() 只关自己的 page

        # 开一个新窗口（新 page），与母代理的 page 彼此独立
        child._page = await self._browser.new_page()
        logger.info("[BrowserManager] 已为子代理开新窗口（共享登录态，零复制）")
        return child

    def _default_args(self):
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-service-autorun",
            "--password-store=basic",
            "--no-sandbox",
            "--disable-gpu",
        ]

    async def _apply_window_icon(self):
        """启动后给 Chromium 窗口换上「仙人球浏览器」图标（标题栏 + 任务栏）。

        通过 QueryFullProcessImageNameW 检查 exe 路径是否含 playwright_browsers/ms-playwright，
        精确过滤我们的 Chromium 窗口，绝不误伤用户自己的 Chrome。
        全程 try/except，best-effort，仅 Windows 非 headless。
        """
        try:
            if sys.platform != "win32" or self.headless:
                return
            if not BROWSER_ICON_PATH.exists():
                logger.debug("未找到浏览器图标文件，跳过换图标")
                return

            import ctypes
            from ctypes import wintypes

            ico = BROWSER_ICON_PATH
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1

            def make_icon(size):
                return user32.LoadImageW(None, str(ico), IMAGE_ICON, size, size, LR_LOADFROMFILE)

            hicon_big = make_icon(256)
            hicon_sm = make_icon(32)
            if not hicon_big and not hicon_sm:
                logger.debug("LoadImage 返回空，跳过换图标")
                return

            # 等待窗口稳定
            await asyncio.sleep(3)

            def is_playwright_chromium(pid: int) -> bool:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not h:
                    return False
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        return False
                    p = buf.value.lower().replace("/", "\\")
                    return (("playwright_browsers" in p) or ("ms-playwright" in p)) and p.endswith("chrome.exe")
                finally:
                    kernel32.CloseHandle(h)

            hits = []
            EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def cb(hwnd, lparam):
                try:
                    if user32.IsWindowVisible(hwnd):
                        cls = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, cls, 256)
                        if cls.value == "Chrome_WidgetWin_1":
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            if pid.value and is_playwright_chromium(pid.value):
                                hits.append(hwnd)
                except Exception:
                    pass
                return True

            user32.EnumWindows(EnumProc(cb), 0)

            n = 0
            for hwnd in hits:
                try:
                    user32.SendMessageTimeoutW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm, 0x0002, 1000, None)
                    user32.SendMessageTimeoutW(hwnd, WM_SETICON, ICON_BIG, hicon_big, 0x0002, 1000, None)
                    n += 1
                    logger.debug(f"图标设置成功 HWND={hwnd}")
                except Exception:
                    pass

            if n:
                logger.info(f"已为浏览器窗口换上仙人球图标（{n} 个）")
            else:
                logger.warning("设置图标失败，未找到 Playwright Chromium 窗口")
        except Exception as e:
            logger.debug(f"_apply_window_icon 异常（不影响启动）: {e}")

    def _cleanup_lock_files(self, data_dir: Path):
        """清理 Chromium 锁文件，防止重复启动冲突"""
        lock_files = ["SingletonLock", "SingletonCookieLock", "SingletonSocketLock", "SingletonPipeline",
                      "Chrome_Port", "chrome_debug_port", "SingletonCookie", "lock.file"]
        for p in data_dir.iterdir():
            if any(x in p.name.lower() for x in lock_files):
                try:
                    p.unlink(missing_ok=True)
                    logger.debug(f"已清理锁文件：{p.name}")
                except Exception:
                    pass

    async def close(self):
        # 子窗口 / 接管窗口：只关自己的 page，绝不动 owner 的浏览器进程 / playwright
        if getattr(self, "_is_child", False) or getattr(self, "_adopted", False):
            if self._page is not None:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = None
            logger.info(f"[BrowserManager] 子/接管窗口已关闭（owner 浏览器保持运行）")
            return

        if self._browser:
            try:
                self._browser.remove_listener("close", self._on_context_closed)
            except Exception:
                pass
            try:
                await self._browser.close()
            except:
                pass
            self._browser = None
        # 【同目录防双开】owner 关闭后摘除 registry 登记，让后续 launch 能重新接管
        try:
            from agent_core import profile_lock as _pl
            _pl.release(self._user_data_dir_override, self)
        except Exception:
            pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None
        self._chrome_pids = None

    async def _load_cookies_from_file(self):
        """从持久化目录加载 cookies（兼容旧目录迁移 + 子代理隔离目录）

        优先顺序：
        1. 当前 user_data_dir 内的 deepseek_cookies.json（子代理复制出来的隔离目录）
        2. 默认共享目录 COOKIE_FILE
        3. 旧目录迁移
        """
        profile_cookie = Path(self._user_data_dir_override) / "deepseek_cookies.json"

        # 旧目录迁移（仅当默认 COOKIE_FILE 不存在时）
        if not COOKIE_FILE.exists():
            old_cookie = USER_DATA_DIR / "deepseek_cookies.json"
            if old_cookie.exists():
                import shutil
                COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(old_cookie), str(COOKIE_FILE))
                logger.info(f"已从旧目录迁移 deepseek_cookies.json")

        candidates = [profile_cookie, COOKIE_FILE]
        for cf in candidates:
            if not cf.exists():
                continue
            try:
                cookies = json.loads(cf.read_text(encoding="utf-8"))
                if self._browser and cookies:
                    await self._browser.add_cookies(cookies)
                    logger.info(f"已加载 {len(cookies)} 条 cookies（来源: {cf}）")
                    return
            except Exception as e:
                logger.warning(f"加载 cookies 失败（{cf}）：{e}")

        logger.info("首次启动，无 cookies 文件")

    async def save_cookies(self):
        """保存当前浏览器 cookies 到持久化文件"""
        if not self._browser:
            return
        try:
            cookies = await self._browser.cookies()
            # 过滤出有用的 cookies
            important = [c for c in cookies if any(k in c.get("name", "") for k in ["session", "token", "uid", "sid", "ds_"])]
            if important:
                COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
                COOKIE_FILE.write_text(json.dumps(important, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info(f"已保存 {len(important)} 条关键 cookies")
        except Exception as e:
            logger.warning(f"保存 cookies 失败：{e}")


    async def _migrate_cookies_to_new_dir(self):
        """迁移 cookies 和 browser_data 到新的 platform 独立目录"""
        # 1. 迁移 cookies.json
        old_cookie = USER_DATA_DIR / "deepseek_cookies.json"
        new_cookie = DEEPSEEK_DATA_DIR / "deepseek_cookies.json"
        if old_cookie.exists() and not new_cookie.exists():
            new_cookie.parent.mkdir(parents=True, exist_ok=True)
            try:
                import shutil
                shutil.copy2(str(old_cookie), str(new_cookie))
                logger.info("已迁移 deepseek_cookies.json 到新目录")
            except Exception as e:
                logger.warning(f"迁移 cookies 失败: {e}")
        
        # 2. 迁移整个 browser_data/Default 目录内容（含 IndexedDB、Local Storage 等登录态）
        old_default = USER_DATA_DIR / "Default"
        new_default = DEEPSEEK_DATA_DIR / "Default"
        if old_default.exists() and not new_default.exists():
            new_default.parent.mkdir(parents=True, exist_ok=True)
            try:
                # 逐个复制关键文件/目录
                for item in old_default.iterdir():
                    dst_item = new_default / item.name
                    if item.is_dir():
                        shutil.copytree(str(item), str(dst_item), dirs_exist_ok=True)
                    else:
                        shutil.copy2(str(item), str(dst_item))
                logger.info(f"已将 browser_data/Default 迁移到 browser_profiles/deepseek/Default")
            except Exception as e:
                logger.warning(f"迁移 Default 目录失败: {e}")
        
        # 3. 迁移整个 browser_data 顶层文件
        if not (DEEPSEEK_DATA_DIR / "Local State").exists():
            DEEPSEEK_DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                for item in USER_DATA_DIR.iterdir():
                    if item.name == "Default":
                        continue  # 已单独处理
                    dst = DEEPSEEK_DATA_DIR / item.name
                    if not dst.exists():
                        if item.is_dir():
                            shutil.copytree(str(item), str(dst))
                        else:
                            shutil.copy2(str(item), str(dst))
                logger.info("已迁移 browser_data 顶层文件")
            except Exception as e:
                logger.warning(f"迁移顶层文件失败: {e}")

    async def navigate(self, url: str = DEEPSEEK_URL):
        # 【僵尸自愈】先做僵尸检测：进程被杀后 _page 可能不是 None 但 context 已死，
        # 直接 goto 必报 Target has been closed。判死则清理，让 launch() 走完整重建。
        await self._clear_zombie()
        # 检测浏览器是否已关闭，自动重启
        needs_restart = False
        if self._page is None or self._browser is None:
            needs_restart = True
        elif hasattr(self._browser, "is_closed"):
            # 浏览器进程被强杀时 is_closed() 可能抛异常而非返回 True，统一按「需重启」处理
            try:
                if self._browser.is_closed():
                    needs_restart = True
                    self._page = None
            except Exception:
                needs_restart = True
                self._page = None

        if needs_restart:
            logger.info("检测到浏览器已关闭，尝试自动重启...")
            await self.launch()
            # Playwright 启动是异步的，launch() 返回后页面可能还没好，多等几秒
            for _retry in range(10):
                if self._page is not None:
                    break
                await asyncio.sleep(0.5)
            if self._page is None:
                # 最后再试一次：手动从 context 取页面
                try:
                    if self._browser is not None and hasattr(self._browser, 'pages'):
                        pages = self._browser.pages
                        if pages:
                            self._page = pages[0]
                        else:
                            self._page = await self._browser.new_page()
                except Exception as _e:
                    logger.warning(f"手动取页面失败: {_e}")
            if self._page is None:
                raise RuntimeError("浏览器重启失败：页面未初始化")

        # Detached-frame recovery: if goto fails, create a fresh page
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as nav_err:
            err = str(nav_err)
            # 【代理故障自愈】WPAD 死代理 → 全部跳转 ERR_PROXY_CONNECTION_FAILED。
            # 与 platform_browser.py 一致：置类级直连标记后【彻底关闭旧浏览器】，
            # 再 launch() 起重带 --no-proxy-server 的干净进程。
            # ⚠️ 关键（2026-09-24 修复「已连接但发不出消息」根因）：
            # 绝不能只在新进程上"复用"页面——launch() 见 _browser 非空会直接
            # return「复用」，旧进程仍走死代理 → 页面级重启失败 → 抛
            # RuntimeError("浏览器已关闭且重启失败") → 后端 Agent 启动失败 →
            # commander 永远 not ready → 所有平台（含已登录的元宝）都发不出消息。
            # 必须先 close() 把 _browser 置空，launch() 才会走完整重建并应用直连。
            if "ERR_PROXY_CONNECTION_FAILED" in err:
                BrowserManager._no_proxy_mode = True
                logger.warning("检测到代理连接失败（ERR_PROXY_CONNECTION_FAILED），"
                               "标记直连模式并彻底重建浏览器")
                try:
                    await self.close()
                except Exception:
                    pass
                self._browser = None
                self._page = None
                try:
                    await self.launch()
                    if self._page is None:
                        raise RuntimeError(f"代理自愈后浏览器重启失败: {nav_err}")
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    logger.info(f"已导航（直连自愈后）：{url}")
                    return
                except Exception as _e2:
                    logger.warning(f"代理自愈重建失败: {_e2}")
                    raise RuntimeError(f"代理自愈失败: {nav_err}") from _e2
            # 其它导航错误（浏览器被关/僵尸等）→ 页面级重建
            logger.warning(f"页面跳转失败: {nav_err}，重建页面...")
            if self._browser:
                # 检查浏览器是否还活着（用多种方式验证）
                browser_alive = True
                try:
                    if hasattr(self._browser, "is_closed") and self._browser.is_closed():
                        browser_alive = False
                except Exception:
                    browser_alive = False
                    pass
                # Close stale page, open fresh one
                try:
                    if self._page and not self._page.is_closed():
                        await self._page.close()
                except:
                    pass
                # 验证浏览器是否还存活，访问 pages 属性可能会抛 TargetClosedError
                try:
                    ctx_pages = [p for p in self._browser.pages if not p.is_closed()]
                except Exception as e:
                    logger.warning(f"访问浏览器页面列表失败: {e}，标记为已关闭")
                    browser_alive = False
                    ctx_pages = []
                if browser_alive and ctx_pages:
                    self._page = ctx_pages[0]
                elif browser_alive:
                    try:
                        self._page = await self._browser.new_page()
                    except Exception as new_page_err:
                        logger.warning(f"创建新页面失败: {new_page_err}，标记为已关闭")
                        browser_alive = False
                        self._page = None
                if not browser_alive or self._page is None:
                    # 最后尝试：完整重启
                    logger.info("页面级重启失败，尝试完整重启浏览器...")
                    await self.launch()
                    if self._page is None:
                        raise RuntimeError(f"浏览器已关闭且重启失败: {nav_err}")
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
                else:
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            else:
                raise RuntimeError(f"浏览器已关闭，需要重新启动: {nav_err}")
        logger.info(f"已导航：{url}")

    async def check_login(self) -> bool:
        """检查是否已登录 DeepSeek
        
        ★★★ 修复（2026-09-12）★★★
        旧逻辑 is_chat_page = "chat.deepseek.com" in url 会把 /sign_in 也判定为聊天页，
        导致未登录时误判"已登录"，子代理继续执行 → 找不到 textarea → Timeout。
        
        新逻辑：
        1. URL 明确包含 /sign_in 或 /login → 未登录
        2. 有用户头像 → 已登录
        3. 在聊天页（不含 sign_in/login）且无登录按钮 → 已登录
        4. 其余情况 → 未登录
        """
        if self._page is None:
            return False
        try:
            url = self._page.url
            
            # ★★★ 关键：sign_in / login 页面一律视为未登录 ★★★
            if any(kw in url for kw in ["/sign_in", "/login", "/register"]):
                logger.warning(f"登录检查：URL 为 {url}（登录/注册页）→ 未登录")
                return False
            
            # 1. 检查用户头像（最可靠）
            user_avatar = self._page.locator('img[alt*="avatar"], [class*="avatar"], [class*="user-icon"]')
            has_avatar = await user_avatar.count() > 0
            
            # 2. 检查登录按钮
            login_btn = self._page.locator('button:has-text("登录"), a:has-text("登录"), button:has-text("Login")')
            has_login_btn = await login_btn.count() > 0
            
            # 3. 检查 textarea（有输入框 = 聊天页 = 已登录）
            has_textarea = await self._page.locator('textarea').count() > 0
            
            # 判断逻辑：
            # - 有头像 → 已登录
            # - 有 textarea 且无登录按钮 → 已登录（聊天页）
            # - 有登录按钮 → 未登录
            # - 都没有 → 检查 URL 是否在聊天页
            logged_in = has_avatar or (has_textarea and not has_login_btn)
            
            logger.info(
                f"登录检查：url={url[:50]}, has_avatar={has_avatar}, "
                f"has_textarea={has_textarea}, has_login_btn={has_login_btn} → logged_in={logged_in}"
            )
            return logged_in
        except Exception as e:
            logger.warning(f"登录检查异常：{e}")
            return False

    async def wait_login(self, timeout: int = 120) -> bool:
        """等待用户登录"""
        import time
        start = time.time()
        print(f"等待登录（最多 {timeout} 秒）...")
        
        while time.time() - start < timeout:
            if await self.check_login():
                await self.save_cookies()
                logger.info("登录成功，凭据已保存")
                return True
            await asyncio.sleep(3)
        
        logger.error("登录超时")
        return False

    async def new_session(self):
        """新建对话：在同一浏览器上下文里【新开一个标签页】作为全新独立对话，
        旧对话页收起（仍保留在平台里），后续发送/看界面都走新标签页。"""
        try:
            ctx = getattr(self._page, "context", None) if self._page is not None else None
            if ctx is None and self._browser is not None:
                try:
                    _ctxs = getattr(self._browser, "contexts", None)
                    ctx = _ctxs[0] if _ctxs else None
                except Exception:
                    ctx = None
            if ctx is not None:
                new_page = await ctx.new_page()
                await new_page.goto(DEEPSEEK_URL, wait_until="domcontentloaded", timeout=60000)
                for sel in ("a:has-text('新对话')", "button:has-text('新对话')",
                            "a:has-text('新建对话')", "button:has-text('新建对话')",
                            "button:has-text('New Chat')", "a[href='/']"):
                    try:
                        btn = new_page.locator(sel).first
                        if await btn.count() > 0:
                            await btn.click()
                            await new_page.wait_for_timeout(1200)
                            break
                    except Exception:
                        continue
                old_page = self._page
                self._page = new_page
                if old_page is not None:
                    try:
                        await old_page.close()
                    except Exception:
                        pass
                logger.info("新对话已创建（新标签页）")
                return
        except Exception as e:
            logger.warning(f"新建会话（新标签页）失败，回退同页: {e}")

        # 回退：同页点击新对话按钮（旧行为）
        if self._page is None:
            await self.navigate()
        try:
            btn = self._page.locator("a[href='/'], a:has-text('新对话'), a:has-text('New Chat'), button:has-text('新对话')")
            if await btn.count() > 0:
                await btn.first.click()
                await self._page.wait_for_load_state("domcontentloaded")
                logger.info("新对话已创建")
        except Exception as e:
            logger.warning(f"新建会话：{e}")
            await self.navigate()

    async def _ensure_page(self, url: str = DEEPSEEK_URL):
        """确保 self._page 是活的、可用的页面。

        修复：旧逻辑只在 self._page is None 时重建。但页面被关闭/崩溃后仍持有旧引用
        （非 None）时，后续 textarea.click() 会抛
        'Locator.click: Target page, context or browser has been closed'。
        这里额外检查 is_closed()，死了就置空并触发 navigate() 自动重建。
        """
        dead = False
        if self._page is None:
            dead = True
        else:
            try:
                if self._page.is_closed():
                    dead = True
                    self._page = None
            except Exception:
                dead = True
                self._page = None
        if dead:
            logger.warning("检测到页面已死/未初始化，自动重建...")
            await self.navigate(url)
        if self._page is None:
            raise RuntimeError("页面重建失败：页面未初始化")
        return self._page

    async def send_message(self, text: str) -> bool:
        """发送消息到 DeepSeek（带页面存活自检 + 崩溃自动重建重试）"""
        async def _attempt():
            await self._ensure_page()
            if "chat.deepseek.com" not in self._page.url:
                await self.navigate()
            # 多种选择器尝试（DeepSeek 页面结构可能变化，按优先级多路探测）
            textarea = None
            selectors = [
                "textarea[placeholder*='问']",
                "textarea[placeholder*='输入']",
                "textarea[placeholder*='Ask']",
                "textarea[placeholder*='Type']",
                "textarea",
                "div[contenteditable='true'][role='textbox']",
                "div[role='textbox']",
                "[contenteditable='true']",
                "div[class*='input'] textarea",
                "div[class*='composer'] textarea",
                "textarea[class*='text']",
                "textarea[placeholder]",
            ]

            for sel in selectors:
                try:
                    el = self._page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        textarea = el
                        logger.info(f"找到输入框：{sel}")
                        break
                except Exception:
                    pass

            # 兜底：用 JS 在整页找所有可见 textarea / contenteditable 元素
            if textarea is None:
                try:
                    candidates = await self._page.evaluate("""() => {
                        const all = [
                            ...document.querySelectorAll('textarea'),
                            ...document.querySelectorAll('[contenteditable="true"]'),
                            ...document.querySelectorAll('[contenteditable]'),
                        ];
                        for (const el of all) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 20) return el.tagName;
                        }
                        return null;
                    }""")
                    if candidates:
                        # 重新定位第一个可交互的
                        textarea = self._page.locator("textarea, [contenteditable='true']").first
                        if await textarea.count() > 0 and await textarea.is_visible():
                            logger.info(f"JS 兜底找到输入框: {candidates}")
                except Exception as e:
                    logger.warning(f"JS 兜底失败: {e}")

            if textarea is None:
                logger.error("未找到输入框，保存截图调试")
                try:
                    await self._page.screenshot(path="debug_no_input.png")
                except Exception:
                    pass
                return False

            # 清空并填写
            await textarea.click()
            await asyncio.sleep(0.2)
            await textarea.fill("")
            await asyncio.sleep(0.1)
            await textarea.fill(text)
            await asyncio.sleep(0.2)

            # 尝试多种发送方式
            sent = False
            for sel in ["button[type='submit']", "button:has-text('发送')", "button:has-text('Send')"]:
                btn = self._page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_enabled():
                    await btn.first.click()
                    logger.info("消息已发送（点击按钮）")
                    sent = True
                    break

            if not sent:
                await textarea.press("Enter")
                logger.info("消息已发送（Enter 键）")
                sent = True

            return sent

        try:
            return await _attempt()
        except Exception as e:
            err = str(e)
            if "has been closed" in err or "TargetClosed" in err or "detached" in err:
                logger.warning(f"发送时页面异常（{e}），重建后重试一次...")
                self._page = None
                try:
                    return await _attempt()
                except Exception as e2:
                    logger.error(f"重建重试仍失败: {e2}")
                    raise
            raise

    async def _send_internal_impl(self, text: str) -> bool:
        """_send_internal 的内部实现（带页面存活自检）。"""
        await self._ensure_page()
        if "chat.deepseek.com" not in self._page.url:
            await self.navigate()

        msg_count_before = await self._page.evaluate("""
            () => document.querySelectorAll('[data-role="user"], .user-message, [class*="user"]').length
        """)

        textarea = None
        for sel in ["textarea[placeholder*='说'], textarea",
                    "div[contenteditable='true'][role='textbox']"]:
            try:
                el = self._page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    textarea = el
                    break
            except Exception:
                pass

        if textarea is None:
            return False

        await textarea.click()
        await asyncio.sleep(0.1)
        await textarea.fill(text)
        await asyncio.sleep(0.1)

        for sel in ["button[type='submit']", "button:has-text('发送')"]:
            btn = self._page.locator(sel)
            if await btn.count() > 0 and await btn.first.is_enabled():
                await btn.first.click()
                break
        else:
            await textarea.press("Enter")

        await asyncio.sleep(0.5)

        try:
            deleted = await self._page.evaluate(f"""
                () => {{
                    const allUser = document.querySelectorAll('[data-role="user"], .user-message, [class*="user_msg"]');
                    if (allUser.length > {msg_count_before}) {{
                        allUser[allUser.length - 1].remove();
                        return true;
                    }}
                    const allMsgs = document.querySelectorAll('[class*="message_content"], [class*="msg_content"], [class*="bubble"]');
                    for (let i = allMsgs.length - 1; i >= 0; i--) {{
                        const el = allMsgs[i];
                        if (el.innerText && el.innerText.trim().startsWith('@@@@')) {{
                            el.remove();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            logger.info(f"内部消息删除结果：{deleted}")
        except Exception as e:
            logger.warning(f"删除内部消息失败：{e}")

        return True

    async def _send_internal(self, text: str) -> bool:
        """内部发送协议消息：发完立即从 DOM 删除，不暴露给用户（带页面存活自检 + 重试）"""
        try:
            return await self._send_internal_impl(text)
        except Exception as e:
            err = str(e)
            if "has been closed" in err or "TargetClosed" in err or "detached" in err:
                logger.warning(f"_send_internal 页面异常（{e}），重建后重试一次...")
                self._page = None
                return await self._send_internal_impl(text)
            raise

    async def wait_response(self, timeout: int = 120, on_thinking=None,
                            thinking_selector: str = "", stop_check=None) -> Optional[str]:
        """等待 AI 回复（可选实时抓取「思考过程」并通过 on_thinking 回调上报）

        改进：每次循环都检测页面是否还活着。如果页面掉线/崩溃，立即返回而不是死等 timeout。
        stop_check：可调用（无参），返回 True 则立即中止等待、返回已抓到的部分文本
        （让「⏹ 中断 / 💬 插话」在等待 AI 期间也真正生效）。
        """
        if self._page is None:
            return None

        logger.info(f"等待回复（超时 {timeout}s）...")
        last_text = ""
        last_thinking = ""
        stable = 0

        for _ in range(timeout):
            await asyncio.sleep(1)
            # 【#85】用户中断立即生效：每个 tick 查 stop_check，命中就中止等待
            if stop_check is not None:
                try:
                    if stop_check():
                        logger.info("等待回复中被 stop_check 中止（中断/插话）")
                        return last_text or None
                except Exception:
                    pass
            try:
                # 先检查页面是否还活着，如果死了立即返回
                if self._page.is_closed():
                    logger.warning("等待回复时发现页面已关闭，立即返回")
                    return last_text or None
                # 检查浏览器上下文是否还活着
                if self._browser and hasattr(self._browser, "is_closed"):
                    try:
                        if self._browser.is_closed():
                            logger.warning("等待回复时发现浏览器已关闭，立即返回")
                            return last_text or None
                    except Exception:
                        logger.warning("等待回复时浏览器上下文检查失败，继续尝试")

                text = await self._page.evaluate("""
                    () => {
                        // 多套选择器兜底，优先 DeepSeek 真实结构：
                        // 助手回答渲染在 .ds-markdown / .message-content，
                        // 整条消息有 [data-message-id] / .message / .ds-message
                        const sels = [".ds-markdown", ".message-content", ".ds-message",
                                      ".markdown-body", ".prose",
                                      "[data-message-id]", "[class*='message']"];
                        let best = "";
                        for (const s of sels) {
                            const els = document.querySelectorAll(s);
                            if (!els.length) continue;
                            const t = (els[els.length - 1].innerText || "").trim();
                            if (t.length > best.length) best = t;
                        }
                        return best || null;
                    }
                """)
                if text and text != last_text:
                    last_text = text
                    stable = 0
                    logger.info(f"收到内容 ({len(text)} 字符)...")
                elif text and text == last_text:
                    stable += 1
                    if stable >= 3:
                        logger.info(f"回复完成：{len(text)} 字符")
                        if on_thinking and last_thinking:
                            try:
                                on_thinking(last_thinking)
                            except Exception:
                                pass
                        return text
                else:
                    loading = await self._page.query_selector(".loading, [class*='generating']")
                    if not loading and last_text:
                        stable += 1
                        if stable >= 2:
                            if on_thinking and last_thinking:
                                try:
                                    on_thinking(last_thinking)
                                except Exception:
                                    pass
                            return last_text
            except Exception as e:
                # 页面操作异常（可能是掉线），记录后继续等，但不再尝试读取内容
                logger.warning(f"等待回复异常：{e}")
                # 如果是 TargetClosed 或类似错误，说明页面已死，立即返回
                err_str = str(e)
                if "TargetClosed" in err_str or "has been closed" in err_str:
                    logger.warning("页面已关闭，提前结束等待")
                    return last_text or None

            # 实时抓取思考过程
            if thinking_selector and on_thinking:
                try:
                    # 把平台配置的选择器传进去（旧实现漏传 → 只有写死的通用选择器
                    # 生效 → DeepSeek 的 .ds-think-content 永远抓不到 → GUI 思考块空白）
                    t = await self.browser_get_thinking_content(thinking_selector)
                    # 兜底：仍取不到就用 thinking_selector 直接查innerText
                    if not t and thinking_selector:
                        t = await self._page.evaluate(
                            "(sel) => {"
                            " const els = document.querySelectorAll(sel);"
                            " let best = '';"
                            " for (const el of els) {"
                            "   const x = (el.innerText || '').trim();"
                            "   if (x && x.length > best.length) best = x;"
                            " } return best;"
                            " }",
                            thinking_selector,
                        )
                        if t:
                            try:
                                from agent_core.session import _strip_thinking_header
                                t = _strip_thinking_header(t)
                            except Exception:
                                pass
                    # ── 思考流去噪 + 去重 ────────────────────────────────
                    # 真机实测发现两类脏数据：
                    #  1) 截断碎片：流式渲染时页面只挂了半个词（实测收到过 "The"），
                    #     这种半截片段画进思考块就是一行莫名其妙的英文残片；
                    #  2) 重复推送：同一段思考文字被连推 2~5 次（同一 ts 附近的
                    #     多次轮询读到同一份 DOM 快照），GUI 上表现为思考块刷屏。
                    # 处理：太短的片段直接丢；与上次内容完全相同、或上次内容已包含
                    # 本次内容的（说明是旧快照），都不再重复上报。
                    _emit = t
                    if _emit:
                        _stripped = _emit.strip()
                        # 过短且没有标点/中文的碎片，视为截断噪声
                        if len(_stripped) <= 3 and not any(
                            ch in _stripped for ch in "。，！？：；、,.!?:;"
                        ):
                            _emit = ""
                        # 完全重复
                        elif _stripped == last_thinking.strip():
                            _emit = ""
                        # 旧快照（本次内容是上次的前缀 → 页面还没渲染出新内容）
                        elif last_thinking.strip().startswith(_stripped):
                            _emit = ""
                    if _emit:
                        last_thinking = t
                        try:
                            on_thinking(t)
                        except Exception:
                            pass
                except Exception:
                    pass

        if on_thinking and last_thinking:
            try:
                on_thinking(last_thinking)
            except Exception:
                pass
        # 诊断：超时时把页面 HTML + 截图落盘，便于定位「为什么没抓到回复」
        try:
            _diag_dir = Path(__file__).resolve().parent.parent
            html = await self._page.content()
            (_diag_dir / "xrz_wait_timeout.html").write_text(html, encoding="utf-8", errors="ignore")
            await self._page.screenshot(path=str(_diag_dir / "xrz_wait_timeout.png"), full_page=False)
            logger.warning(f"wait_response 超时（{timeout}s），已落盘诊断: "
                           f"{_diag_dir / 'xrz_wait_timeout.html'}")
        except Exception as e:
            logger.warning(f"wait_response 诊断落盘失败: {e}")
        return last_text if last_text else None

    async def browser_click(self, selector: str) -> str:
        if self._page is None:
            return "错误：页面未初始化"
        try:
            await self._page.locator(selector).first.click()
            return f"已点击：{selector}"
        except Exception as e:
            return f"点击失败：{e}"

    async def browser_fill(self, selector: str, text: str) -> str:
        if self._page is None:
            return "错误：页面未初始化"
        try:
            await self._page.locator(selector).first.fill(text)
            return f"已填写 {selector}"
        except Exception as e:
            return f"填写失败：{e}"

    async def browser_screenshot(self, path: str) -> str:
        if self._page is None:
            return "错误：页面未初始化"
        try:
            await self._page.screenshot(path=path, full_page=True)
            return f"截图已保存：{path}"
        except Exception as e:
            return f"截图失败：{e}"

    async def browser_get_text(self, selector: str) -> str:
        if self._page is None:
            return "错误：页面未初始化"
        try:
            return await self._page.locator(selector).first.inner_text()
        except Exception as e:
            return f"获取文本失败：{e}"

    async def browser_get_thinking_content(self, thinking_selector: str = "") -> str:
        """获取当前深度思考内容（如果正在显示）。

        【2026-09-21 真机修复·用户报「思考过程显示有大问题」的根因】
        旧实现只按写死的 6 个通用选择器找（[class*='thinking'] / .reasoning-content …），
        这些【全部不匹配 DeepSeek 真实的思考面板 class `.ds-think-content`】——
        真机 /probe 实测：写死选择器 0 匹配，而 `.ds-think-content .ds-markdown`
        稳定命中并返回上百字真实推理。结果就是整轮任务里 on_thinking 一次都不触发，
        GUI 的「🧠 模型推理（深度思考）」块永远是空的。

        修法：把「平台配置的 thinking_selector」提到最前面（platforms.json 已按真机 DOM
        逐个平台核对），只有它取不到才回落到通用选择器。同时按 DeepSeek 的实际层级
        优先取内层 .ds-markdown，避免把折叠条标题「已思考（用时 N 秒）」当成推理正文。
        """
        if self._page is None:
            return ""
        # 候选顺序：平台配置优先 → 通用兜底
        selectors = []
        if thinking_selector:
            selectors.append(thinking_selector)
        selectors += [
            ".ds-think-content .ds-markdown",   # DeepSeek 真机命中（推理正文层）
            ".ds-think-content",                # DeepSeek 兜底（含标题，下面会剥）
            "[class*='thinking']",
            "[class*='deep-think']",
            "[class*='reasoning']",
            "[class*='思考']",
            ".thinking-content",
            ".reasoning-content",
        ]
        try:
            for sel in selectors:
                try:
                    elem = self._page.locator(sel).first
                    if await elem.is_visible(timeout=500):
                        txt = await elem.inner_text()
                        if not txt or not txt.strip():
                            continue
                        # 剥掉折叠条标题行（grep 不出正文 vs 标题的区别时以内容为准）
                        try:
                            from agent_core.session import _strip_thinking_header
                            txt = _strip_thinking_header(txt)
                        except Exception:
                            pass
                        if txt and txt.strip():
                            return txt
                except Exception:
                    continue
            return ""
        except Exception:
            return ""

    async def browser_get_html(self) -> str:
        if self._page is None:
            return "错误：页面未初始化"
        try:
            return await self._page.content()
        except Exception as e:
            return f"获取 HTML 失败：{e}"

    async def upload_file(self, file_paths) -> str:
        """上传一个或多个文件（图片/PDF 等）到当前聊天输入框。

        直接定位隐藏的 input[type=file] 并用 set_input_files 设值
        （绕开系统文件对话框，Playwright 原生支持，最稳）。
        """
        if self._page is None:
            return "错误：页面未初始化"
        from pathlib import Path as _P
        valid = [str(_P(fp)) for fp in file_paths if _P(fp).exists()]
        if not valid:
            return "错误：没有有效文件可上传（路径不存在）"
        file_input = self._page.locator("input[type='file']").first
        if await file_input.count() == 0:
            return "错误：未找到文件上传入口（DeepSeek 可能需登录后才有附件按钮）"
        try:
            if len(valid) > 1:
                await file_input.set_input_files(valid)
            else:
                await file_input.set_input_files(valid[0])
            await asyncio.sleep(1.5)
            names = ", ".join(_P(v).name for v in valid)
            logger.info(f"已上传 {len(valid)} 个文件: {names}")
            return f"已上传 {len(valid)} 个文件: {names}"
        except Exception as e:
            return f"文件上传失败: {e}"

    async def toggle_deep_think(self, enable: bool = True) -> bool:
        """切换深度思考模式
        
        支持多平台：DeepSeek、通义千问、豆包、元宝、ChatGPT、Gemini
        """
        if self._page is None:
            return False
        try:
            # 尝试多种平台的深度思考按钮选择器
            selectors_by_platform = {
                "deepseek": [
                    "button:has-text('深度思考')",
                    "button:has-text('DeepThink')",
                    "button:has-text('深度')",
                    "[class*='deep'] button",
                    "[class*='think'] button",
                    "div[role='button']:has-text('深度思考')",
                    "div[role='button']:has-text('深度')",
                ],
                "tongyi": [
                    "button:has-text('深度思考')",
                    "button:has-text('深度')",
                    "[class*='think'] button",
                ],
                "gpt": [
                    "button:has-text('Analysis')",
                    "button:has-text('Extended')",
                    "[class*='analysis'] button",
                ],
                "gemini": [
                    "button:has-text('Think')",
                    "[class*='think'] button",
                ],
            }
            
            all_selectors = []
            for sel_list in selectors_by_platform.values():
                all_selectors.extend(sel_list)
            
            for sel in all_selectors:
                try:
                    btn = self._page.locator(sel).first
                    
                    if await btn.count() > 0:
                        # 检查当前状态
                        is_active = await btn.evaluate("""el => {
                            const computed = window.getComputedStyle(el);
                            const bg = computed.backgroundColor || '';
                            const hasActiveClass = el.classList.contains('active') || 
                                                  el.classList.contains('selected') ||
                                                  el.classList.contains('enabled') ||
                                                  el.classList.contains('thinking');
                            const ariaPressed = el.getAttribute('aria-pressed') === 'true';
                            const ariaExpanded = el.getAttribute('aria-expanded') === 'true';
                            const isChecked = el.getAttribute('aria-checked') === 'true';
                            // 检查背景色是否为蓝色/紫色（激活状态常见特征）
                            const isColored = bg.includes('rgb(0') || bg.includes('blue') || bg.includes('#1890ff') || bg.includes('purple');
                            return hasActiveClass || ariaPressed || ariaExpanded || isChecked || isColored;
                        }""")
                        
                        target_state = enable
                        if is_active == target_state:
                            logger.info(f"深度思考模式已是{'开启' if enable else '关闭'}状态")
                            return True
                        
                        await btn.click()
                        await asyncio.sleep(0.8)
                        logger.info(f"深度思考模式已{'开启' if enable else '关闭'}")
                        return True
                except Exception:
                    continue
            
            logger.warning("未找到深度思考按钮 - 请检查界面是否有变化")
            return False
        except Exception as e:
            logger.warning(f"切换深度思考模式失败：{e}")
            return False

    async def is_deep_think_active(self) -> bool:
        """检查深度思考模式是否激活"""
        if self._page is None:
            return False
        try:
            selectors = [
                "button:has-text('深度思考')",
                "button:has-text('DeepThink')",
            ]
            
            for sel in selectors:
                btn = self._page.locator(sel).first
                if await btn.count() > 0:
                    is_active = await btn.evaluate("el => el.classList.contains('active') || el.getAttribute('aria-pressed') === 'true' || el.classList.contains('selected')")
                    return bool(is_active)
            
            return False
        except Exception:
            return False


def copy_credentials_to_managed_dir():
    """将浏览器数据目录的凭据复制到凭据管理目录"""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    
    if COOKIE_FILE.exists():
        shutil.copy2(COOKIE_FILE, MANAGED_COOKIE_FILE)
        logger.info(f"凭据已复制到：{MANAGED_COOKIE_FILE}")
        return True
    return False
