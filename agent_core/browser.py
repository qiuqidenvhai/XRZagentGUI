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

    @property
    def context(self):
        return self._browser

    @property
    def page(self):
        return self._page

    async def launch(self):
        if self._browser is not None:
            logger.info("浏览器已启动，复用")
            return

        from playwright.async_api import async_playwright

        logger.info("准备启动持久化 Chromium 浏览器...")

        # 使用指定的用户数据目录或默认的
        user_data_dir = self._user_data_dir_override
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
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
        except Exception as e:
            logger.warning(f"持久化上下文创建失败: {e}，尝试临时目录")
            tmp_dir = tempfile.mkdtemp(prefix="xrz_chrome_")
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=_launch_args,
                accept_downloads=True,
            )
            logger.info(f"临时上下文创建成功: {tmp_dir}")

        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        # 4. 加载 cookies
        await self._migrate_cookies_to_new_dir()
        await self._load_cookies_from_file()

        # 5. 等 Chromium 窗口创建出来后，换上仙人球浏览器图标（best-effort）
        await asyncio.sleep(1.5)
        await self._apply_window_icon()

        logger.info("浏览器启动完成")
        self._launched = True

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

        实现：通过 CDP `Browser.getWindowForTarget` 拿到【我们这个浏览器实例】
        window（窗口句柄），再 WM_SETICON 设大小图标。全程 try/except，best-effort，
        绝不扫描/枚举系统里其它 Chromium 窗口（避免误改用户自己的 Chrome）。
        仅 Windows 非 headless。
        """
        try:
            if sys.platform != "win32" or self.headless:
                return
            if not BROWSER_ICON_PATH.exists():
                logger.debug("未找到浏览器图标文件，跳过换图标")
                return

            import win32gui  # type: ignore
            import win32con  # type: ignore

            hicon_big = win32gui.LoadImage(
                0, str(BROWSER_ICON_PATH), win32con.IMAGE_ICON,
                256, 256, win32con.LR_LOADFROMFILE,
            )
            hicon_sm = win32gui.LoadImage(
                0, str(BROWSER_ICON_PATH), win32con.IMAGE_ICON,
                16, 16, win32con.LR_LOADFROMFILE,
            )
            if not hicon_big or not hicon_sm:
                logger.debug("LoadImage 返回空，跳过换图标")
                return

            # 优先：通过 CDP 精确拿到我们这个 Chromium 的窗口句柄
            targets_hwnd: list = []
            try:
                cdp = await self._browser.new_cdp_session(self._page)
                try:
                    tgts = await cdp.send("Target.getTargets")
                    page_tgt = None
                    for t in (tgts.get("targetInfos") or []):
                        if t.get("type") == "page":
                            page_tgt = t.get("targetId")
                            break
                    if page_tgt:
                        winfo = await cdp.send(
                            "Browser.getWindowForTarget",
                            {"targetId": page_tgt},
                        )
                        wid = winfo.get("windowId")
                        if wid:
                            targets_hwnd.append(wid)
                            logger.debug(f"CDP 拿到我们的 HWND={wid}")
                finally:
                    try:
                        await cdp.detach()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"CDP 取 HWND 失败: {e}")

            if not targets_hwnd:
                # 拿不到精确窗口就放弃，绝不枚举其它 Chrome 窗口（保护用户 Chrome）
                logger.debug("未能定位到本浏览器窗口，跳过换图标（不影响启动）")
                return

            n_ok = 0
            for hwnd in targets_hwnd:
                try:
                    win32gui.SendMessage(hwnd, win32con.WM_SETICON,
                                         win32con.ICON_BIG, hicon_big)
                    win32gui.SendMessage(hwnd, win32con.WM_SETICON,
                                         win32con.ICON_SMALL, hicon_sm)
                    n_ok += 1
                except Exception as e:
                    logger.debug(f"窗口 {hwnd} 换图标失败: {e}")
            if n_ok:
                logger.info(f"已为浏览器窗口换上仙人球图标（{n_ok} 个）")
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
        # 子窗口：只关自己的 page，绝不动母代理的浏览器进程 / playwright
        if getattr(self, "_is_child", False):
            if self._page is not None:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = None
            logger.info("[BrowserManager] 子窗口已关闭（母代理浏览器保持运行）")
            return

        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None

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
        if self._page is None:
            raise RuntimeError("页面未初始化")
        # Detached-frame recovery: if goto fails, create a fresh page
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as nav_err:
            logger.warning(f"页面跳转失败: {nav_err}，重建页面...")
            if self._browser:
                # Close stale page, open fresh one
                try:
                    await self._page.close()
                except:
                    pass
                ctx_pages = [p for p in self._browser.pages if not p.is_closed()]
                if ctx_pages:
                    self._page = ctx_pages[0]
                else:
                    self._page = await self._browser.new_page()
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            else:
                raise
        logger.info(f"已导航：{url}")

    async def check_login(self) -> bool:
        """检查是否已登录 DeepSeek"""
        if self._page is None:
            return False
        try:
            # 多种登录状态检测方式
            # 1. 检查是否有登录按钮
            login_btn = self._page.locator('button:has-text("登录"), a:has-text("登录"), button:has-text("Login")')
            has_login_btn = await login_btn.count() > 0
            
            # 2. 检查是否有用户头像/设置
            user_avatar = self._page.locator('img[alt*="avatar"], [class*="avatar"], [class*="user-icon"]')
            has_avatar = await user_avatar.count() > 0
            
            # 3. 检查 URL 是否在聊天页面
            is_chat_page = "chat.deepseek.com" in self._page.url
            
            # 如果有头像或者在聊天页面且没有登录按钮，则认为已登录
            logged_in = has_avatar or (is_chat_page and not has_login_btn)
            
            logger.info(f"登录检查：has_login_btn={has_login_btn}, has_avatar={has_avatar}, is_chat_page={is_chat_page}, logged_in={logged_in}")
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
        """新建对话"""
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

    async def send_message(self, text: str) -> bool:
        """发送消息到 DeepSeek"""
        if self._page is None:
            raise RuntimeError("页面未初始化")
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
            await self._page.screenshot(path="debug_no_input.png")
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

    async def _send_internal(self, text: str) -> bool:
        """内部发送协议消息：发完立即从 DOM 删除，不暴露给用户"""
        if self._page is None:
            raise RuntimeError("页面未初始化")
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

    async def wait_response(self, timeout: int = 120, on_thinking=None,
                            thinking_selector: str = "") -> Optional[str]:
        """等待 AI 回复（可选实时抓取「思考过程」并通过 on_thinking 回调上报）"""
        if self._page is None:
            return None

        logger.info(f"等待回复（超时 {timeout}s）...")
        last_text = ""
        last_thinking = ""
        stable = 0

        for _ in range(timeout):
            await asyncio.sleep(1)
            try:
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
                logger.warning(f"等待回复异常：{e}")

            # 实时抓取思考过程
            if thinking_selector and on_thinking:
                try:
                    t = await self.browser_get_thinking_content()
                    # browser_get_thinking_content 已按固定选择器抓，这里用 thinking_selector 兜底
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
                    if t and t != last_thinking:
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

    async def browser_get_thinking_content(self) -> str:
        """获取当前深度思考内容（如果正在显示）"""
        if self._page is None:
            return ""
        try:
            # 尝试多种可能的深度思考内容选择器
            selectors = [
                "[class*='thinking']",
                "[class*='deep-think']",
                "[class*='reasoning']",
                "[class*='思考']",
                ".thinking-content",
                ".reasoning-content",
            ]
            for sel in selectors:
                try:
                    elem = self._page.locator(sel).first
                    if await elem.is_visible(timeout=500):
                        return await elem.inner_text()
                except:
                    continue
            return ""
        except Exception as e:
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
