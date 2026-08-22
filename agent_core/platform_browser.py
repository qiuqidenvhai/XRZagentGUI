"""
platform_browser.py — 多平台浏览器管理器

核心架构：
1. 每个 AI 平台独立 BrowserManager 实例（独立 Chromium 进程）
2. 子代理如果是同一平台，复用父级的 BrowserManager
3. 支持文件上传
4. 每个平台页面深度适配
5. 从 browser_data 目录迁移 cookies 到新目录

支持平台：
- DeepSeek: https://chat.deepseek.com
- 通义千问: https://tongyi.aliyun.com/qianwen/
- 豆包: https://www.doubao.com/chat/
- 元宝: https://yuanbao.tencent.com/chat/
"""
import asyncio
import logging
import json
import shutil
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger("platform_browser")

# 网页自带的 UI 文字（开关/按钮标签/免责声明等），绝不能当作 AI 回复读进上下文。
# 这些文字一旦被当成 assistant 回复读进去，下一次又会作为上下文发给模型，造成污染。
# 典型来源（chat.qwen.ai）：
#   - 输入框旁的「快速回答」开关文字
#   - 回复底部的免责声明「人工智能生成的内容可能不准确」
#   - 回复操作按钮「复制 / 赞 / 踩 / 重新生成 / 分享」
UI_NOISE_TOKENS = [
    # qwen「快速回答」开关 / 模式标签
    "快速回答", "快速",
    # 各平台回复底部的免责声明（多种写法）
    "人工智能生成的内容可能不准确",
    "AI生成的内容可能不准确",
    "以上内容由人工智能生成，仅供参考",
    "以上内容由 AI 生成，仅供参考",
    "内容由人工智能生成，仅供参考",
    "内容由 AI 生成，仅供参考",
    "生成内容仅供参考",
    "内容可能不准确",
    "以上内容为AI生成",
    # 回复操作按钮
    "重新生成", "重新回答", "重新编辑",
    "复制", "复制代码", "编辑", "赞", "踩", "分享", "更多",
    "朗读", "停止", "继续生成",
]


# 浏览器数据目录（全部落在 D 盘项目目录内，绝不写 C:\Users\...）
from agent_core.xrz_paths import BROWSER_DATA_OLD, BROWSER_DATA_ROOT, DATA_ROOT

@dataclass
class PlatformProfile:
    """平台浏览器配置文件"""
    platform: str  # deepseek, tongyi, doubao, yuanbao
    name: str
    url: str
    chat_url: str
    input_selector: str
    send_selector: str  # 可以为空，表示用 Enter 发送
    upload_button_selector: str  # 文件上传按钮选择器，空字符串表示不支持
    response_selector: str
    thinking_selector: str = ""  # 抓取「思考过程」的选择器，空字符串表示不抓取
    # ── 开启「深度思考」的按钮选择器（每个平台 UI 不同，按优先级顺序尝试）──
    # 为什么需要逐个平台配：DeepSeek 是「深度思考(R1)」toggle 按钮；通义是
    # 输入框上方的模式开关；豆包/元宝是各自独立的思考模式入口。开启方式不一，
    # 不能用单一选择器通吃，必须按平台列出候选。
    thinking_toggle_selectors: list = field(default_factory=list)
    # 深度思考控件类型：
    #   "toggle" —— 点击即切换的开关按钮（如 DeepSeek 的 div.ds-toggle-button）
    #   "select" —— 点击展开下拉、再选「深度思考」选项（如 Qwen 的 ant-select 下拉框）
    thinking_mode: str = "toggle"
    # 文件上传入口：点击后触发隐藏 file input 的元素选择器。
    # 空字符串表示「不点按钮、直接找 input[type=file] 用 set_input_files 设值」
    # （绝大多数现代聊天平台都有隐藏 file input，直接设值最稳，绕开系统文件对话框）。
    attach_button_selector: str = ""
    # 部分平台（如千问 chat.qwen.ai）点开「+」按钮后是个下拉菜单，需要再点菜单里的
    # 「上传附件」项才会真正激活 file input。此字段填菜单项文字；为空表示点完
    # attach_button_selector 后直接找 file input 即可。
    attach_menu_item_text: str = ""
    # 是否支持一次上传多个文件
    multi_file: bool = True
    # 「生成中」信号选择器：该元素在 AI 生成期间存在且可见，生成完成即消失。
    # 用于 wait_response 以「浏览器 UI 信号」判断是否输出完毕（而非靠等待时间）。
    #   - qwen/tongyi: ".stop-button"（生成时出现，完成后被移除，换成 .send-button）
    #   - deepseek:    ".loading, [class*='generating']"（流式时存在，完成后消失）
    #   - doubao/yuanbao: 同上思路，用停止按钮类。留空则回落到「文本稳定」兜底。
    generating_selector: str = ""
    viewport_width: int = 1280
    viewport_height: int = 900
    
    # 登录检测
    logged_in_selectors: list = field(default_factory=lambda: [
        'button:has-text("登录")',  # 如果找不到登录按钮，说明已登录
    ])
    # 未登录标记文字（按钮/链接文案）：命中任一条即视为「未登录」。
    # 用于在通用 check_login 里识别 ChatGPT("Log in")/Claude("Sign in")/Grok 等
    # 非中文登录入口——这是「适配更多网页 AI」的关键扩展点（纯配置即可新增平台）。
    login_texts: list = field(default_factory=list)
    # GUI 展示用：侧边栏按钮文字与图标（纯展示，缺失时回退 name / 🌐）
    display: str = ""
    icon: str = "🌐"

PLATFORM_PROFILES = {
    "deepseek": PlatformProfile(
        platform="deepseek",
        name="DeepSeek",
        url="https://chat.deepseek.com",
        chat_url="https://chat.deepseek.com",
        input_selector="textarea",
        send_selector="button[type='submit']",
        upload_button_selector="",  # 保留字段（兼容旧逻辑）；实际用 attach_button_selector
        # 深度思考开关：真实 DOM 确认是 div.ds-toggle-button（文字「深度思考」，
        # 开启时 class 含 ds-toggle-button--selected）。用 :has-text 精确锁定该按钮。
        thinking_toggle_selectors=[
            "div[class*='ds-toggle-button']:has-text('深度思考')",
            "div.ds-toggle-button:has-text('深度思考')",
            "[class*='ds-toggle-button']:has-text('深度思考')",
        ],
        # 文件上传：DeepSeek 有隐藏 input[type=file]（accept 含 pdf/png/docx，multiple），
        # 直接用 upload_files 策略1（set_input_files 到隐藏 input）即可，无需点按钮。
        attach_button_selector="",
        multi_file=True,
        response_selector="[class*='message']:last-child",
        generating_selector=".loading, [class*='generating']",
    ),
    "tongyi": PlatformProfile(
        platform="tongyi",
        name="通义千问",
        url="https://chat.qwen.ai/",
        chat_url="https://chat.qwen.ai/",
        input_selector="textarea, [contenteditable='true']",
        send_selector="",  # 用 Enter 发送（chat.qwen.ai 输入框回车即发送）
        upload_button_selector="",  # 保留字段（兼容旧逻辑）；实际用 attach_button_selector
        # 注意：不要加 main [class*='chat'] 这种过宽选择器——它会匹配整个聊天大容器
        # （连输入框的「快速回答」开关、整段历史都包进去），导致读到的「回复」其实是
        # 网页自带 UI 文字 + 整段对话。只用消息级选择器，配合 _read_last_reply 的噪音清洗。
        # 深度思考开关：真实 DOM 确认是 Ant Design 下拉框 .qwen-select-thinking
        # （ant-select-single），点击 .ant-select-selector 展开后选「深度思考」选项。
        # thinking_mode="select" 走专门的下拉选择分支。
        thinking_mode="select",
        thinking_toggle_selectors=[
            "div[class*='qwen-select-thinking'] .ant-select-selector",
            "div.qwen-select-thinking .ant-select-selector",
            "div.qwen-select-thinking",
            "div.qwen-thinking-selector",
        ],
        # 文件上传：chat.qwen.ai 的输入区左侧有个「+」按钮（aria-label=选择模式），
        # 点开后下拉菜单里有「上传附件」项，点它才会真正激活隐藏的 #filesUpload
        # （直接对 #filesUpload 调 set_input_files 千问的 React 收不到，表现为"假上传"）。
        # 因此走「点 + 按钮 → 点『上传附件』菜单项 → set_input_files」两步流程。
        attach_button_selector="[aria-label='选择模式']",
        attach_menu_item_text="上传附件",
        multi_file=True,
        response_selector="[class*='message'], [data-testid*='message']",
        generating_selector=".stop-button",
        # chat.qwen.ai 的「思考」区块：Qwen3/QwQ 思考过程会渲染在带 thinking/reasoning 类名的容器里
        thinking_selector="[class*='thinking'], [class*='reasoning'], [class*='think'], [data-testid*='thinking'], details:has(summary)",
    ),
    "doubao": PlatformProfile(
        platform="doubao",
        name="豆包",
        url="https://www.doubao.com/chat/",
        chat_url="https://www.doubao.com/chat/",
        input_selector="textarea.semi-input-textarea",
        send_selector="",  # 用 Enter 发送
        upload_button_selector="",  # 保留字段；实际用 attach_button_selector + 隐藏 file input
        # 深度思考开关：豆包底部工具栏的「🔬 深入研究」按钮（真机确认，非「深度思考」）
        # 该按钮是 <button> data-skill-id="skill_bar_button_25"，点击前后 class/aria 不变，
        # 普通 _detect_toggle_active 无法判断状态，需走 _enable_thinking_doubao 特殊处理。
        thinking_mode="toggle",
        thinking_toggle_selectors=[
            "button:has-text('深入研究')",          # 真实文字（2026-07-18 真机确认）
            "button:has-text('深度思考')",           # 兼容可能的其他命名
            "[class*='deep-research']",
            "[class*='deepResearch']",
            "[class*='deepthink']",
            "div[role='switch']:has-text('深度')",
        ],
        # 文件上传：豆包直接有隐藏 input[type=file]（accept 含 pdf/png/jpg），
        # 不需要点 attach_button；保留空选择器作为 fallback。
        attach_button_selector="",
        multi_file=True,
        response_selector="[class*='message'], [class*='answer']",
        thinking_selector="[class*='thinking'], [class*='reasoning'], [class*='think']",
        generating_selector=".stop-button, [class*='stop']",
    ),
    "yuanbao": PlatformProfile(
        platform="yuanbao",
        name="元宝",
        url="https://yuanbao.tencent.com/chat/",
        chat_url="https://yuanbao.tencent.com/chat/",
        input_selector="[contenteditable].ql-editor.ql-blank",
        send_selector="",  # 用 Enter 发送
        upload_button_selector="span.icon-upload",  # 保留字段（兼容旧逻辑）
        # 深度思考开关：元宝（Hunyuan）思考模式（未登录看不到，候选+诊断兜底）
        thinking_mode="toggle",
        thinking_toggle_selectors=[
            "button:has-text('深度思考')",
            "[class*='think']",
            "div[role='switch']:has-text('深度')",
            ".ant-select-selector",                    # 可能是下拉框（类似 Qwen）
            "button:has-text('深思')",
            "[class*='reasoning'] button",
            "[class*='deep'] button",
        ],
        # 文件上传：元宝输入区右侧 UploadFileSelector（真机 DOM 确认 2026-07-18）。
        # 初始无 input[type=file]，需先点击该容器后才出现隐藏 input。
        attach_button_selector=".UploadFileSelector_iconContainer__6Wpsp, .UploadFileSelector_iconButton__LEwqk, [class*='UploadFileSelector']",
        multi_file=True,
        response_selector="[class*='message'], [class*='answer']",
        thinking_selector="[class*='thinking'], [class*='reasoning'], [class*='think']",
        generating_selector=".stop-button, [class*='stop'], [class*='generating']",
    ),
}


# ============================================================
# 配置化平台注册表（关键升级：适配更多网页 AI）
# ------------------------------------------------------------
# 平台不再写死在代码里。优先级（后者覆盖前者）：
#   1) 代码内置 PLATFORM_PROFILES（上面 4 个，作兜底）
#   2) agent_core/platforms.json   —— 随包发布的内置平台清单
#   3) <数据目录>/platforms.user.json —— 用户自定义/覆盖（部署时改这个即可，不动代码）
# 新增一个网页 AI 只需要往 platforms.json / platforms.user.json 加一段配置，
# 无需改动任何 Python 代码，GUI 与命令行会自动识别。
# ============================================================

_PLATFORM_FIELDS = {f for f in PlatformProfile.__dataclass_fields__}


def _profile_from_dict(d: dict) -> PlatformProfile:
    """把 JSON dict 安全转换为 PlatformProfile（忽略未知字段，缺失字段用默认值）。"""
    clean = {k: v for k, v in d.items() if k in _PLATFORM_FIELDS}
    return PlatformProfile(**clean)


def _merge_profiles_from_json():
    """把 platforms.json（内置）+ platforms.user.json（用户覆盖）合并进 PLATFORM_PROFILES。"""
    global PLATFORM_PROFILES
    sources = [
        (Path(__file__).parent / "platforms.json", "内置"),
        (DATA_ROOT / "platforms.user.json", "用户"),
    ]
    added, overridden = [], []
    for path, tag in sources:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[平台注册表] 读取 {tag}配置失败({path}): {e}")
            continue
        if not isinstance(data, dict):
            logger.warning(f"[平台注册表] {tag}配置格式错误（应为对象）: {path}")
            continue
        for key, prof in data.items():
            if not isinstance(prof, dict):
                continue
            prof.setdefault("platform", key)
            if key in PLATFORM_PROFILES:
                overridden.append(key)
            else:
                added.append(key)
            PLATFORM_PROFILES[key] = _profile_from_dict(prof)
        logger.info(f"[平台注册表] 已合并 {tag}配置: {path}（新增 {len(added)}，覆盖 {len(overridden)}）")
    if added or overridden:
        logger.info(f"[平台注册表] 当前支持平台: {', '.join(sorted(PLATFORM_PROFILES.keys()))}")


# 模块导入时立即合并（保证 multi_browser.py 等 import 到的是合并后的清单）
_merge_profiles_from_json()


def list_platforms() -> list:
    """返回所有已注册平台的精简信息（供 /platforms API 与 GUI 动态渲染）。"""
    out = []
    for key, p in PLATFORM_PROFILES.items():
        name = getattr(p, "name", key)
        out.append({
            "key": key,
            "name": name,
            "display": getattr(p, "display", "") or name,
            "icon": getattr(p, "icon", "") or "🌐",
            "url": getattr(p, "url", ""),
            "chat_url": getattr(p, "chat_url", getattr(p, "url", "")),
        })
    return out


class PlatformBrowserManager:
    """
    单平台浏览器管理器
    
    每个平台独立 Chromium 进程，拥有独立的 cookies 和登录态。
    """
    
    def __init__(self, platform_key: str, headless: bool = False):
        if platform_key not in PLATFORM_PROFILES:
            raise ValueError(f"未知平台: {platform_key}")
        
        self.platform_key = platform_key
        self.profile = PLATFORM_PROFILES[platform_key]
        self.headless = headless
        
        # 用户数据目录（每个平台独立）
        self.user_data_dir = BROWSER_DATA_ROOT / platform_key
        
        # Playwright 对象
        self._playwright = None
        self._browser = None  # Context
        self._page = None
        
    def _kill_stale_browser_for_profile(self):
        """启动前：杀掉仍占用本平台 user_data_dir 的残留 Chromium 进程。

        为什么要做这个：切换平台时若上一次运行/崩溃留下一个还活着的 Chromium 仍在
        使用该 profile 目录，新启动的 Chromium 会抢同一目录 → 表现为「一启动就被关」
        （goto 报 Target page/context/browser has been closed）。仅按命令行里的
        user_data_dir 精确匹配本平台目录，不会误杀 DeepSeek / GUI 的浏览器。
        仅在 Windows 上执行；失败静默跳过，不影响主流程。
        """
        if os.name != "nt":
            return
        try:
            profile = str(self.user_data_dir).replace("/", "\\")
            ps = (
                "Get-CimInstance Win32_Process -Filter "
                "\"Name LIKE '%chrome%' OR Name LIKE '%headless_shell%'\" | "
                "Where-Object { $_.CommandLine -and $_.CommandLine -like '*"
                + profile.replace("'", "''") + "*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20,
            )
            logger.info(f"[{self.profile.name}] 已尝试清理占用本目录的残留浏览器进程")
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 清理残留浏览器进程跳过: {e}")

    async def launch(self, fresh_profile: bool = False):
        """启动浏览器

        稳定性要点（解决「切到通义/豆包等第三方平台时浏览器一启动就被关、goto 报
        Target page/context/browser has been closed」）：

        1) 启动前杀掉仍占用本平台目录的残留 Chromium（上一次崩溃留下的活进程会锁目录，
           导致本次启动的 Chromium 抢目录直接崩）。
        2) 【关键修复】启动参数与 DeepSeek 浏览器保持一致（极简），**不要**加
           `--disable-gpu` / `--disable-software-rasterizer` / `--disable-dev-shm-usage`。
           实测在 Windows 有头 Chromium 上，这些参数会让 GPU/SwiftShader 进程初始化异常，
           表现为「浏览器启动即崩溃 / goto 报 browser has been closed」。DeepSeek 一直用
           极简参数且工作正常，平台浏览器应保持一致。
        3) 给上下文挂 `close` 监听，浏览器若异常关闭会打日志，便于日后定位。
        4) `fresh_profile=True` 时改用一个全新的临时 user_data_dir 启动——这是最后的
           兜底：若原目录因上次崩溃损坏/被锁死、怎么清都起不来，新目录保证浏览器一定能开
           （代价：需重新登录一次，登录后 cookie 会存到新目录）。
        """
        from playwright.async_api import async_playwright

        if self._browser is not None and not fresh_profile:
            logger.info(f"{self.profile.name} 浏览器已启动")
            return

        logger.info(f"[{self.profile.name}] 启动浏览器...")

        # 0. 迁移 cookies（如果旧目录存在 cookies，新目录不存在）
        self._migrate_cookies_from_old_dir()

        # 1. 启动 Playwright
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        # 2. 确定用户数据目录（fresh_profile 时用全新临时目录，绕过损坏/被锁的原目录）
        if fresh_profile:
            from datetime import datetime as _dt
            stamp = _dt.now().strftime("%Y%m%d%H%M%S")
            self.user_data_dir = BROWSER_DATA_ROOT / f"{self.platform_key}_fresh_{stamp}"
            logger.warning(f"[{self.profile.name}] 使用全新临时目录启动: {self.user_data_dir}")
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        # 2.1 先清残留进程（锁目录的主因），再清锁文件
        self._kill_stale_browser_for_profile()
        self._cleanup_lock_files()

        # 公共启动参数：与 DeepSeek(BrowserManager) 保持一致，极简、稳定。
        # 不要加 --disable-gpu 等，Windows 有头模式加它们反而会导致浏览器启动即崩溃。
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-service-autorun",
            "--window-size=1280,860",
        ]

        # 3. 创建持久化上下文
        try:
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                viewport={"width": self.profile.viewport_width, "height": self.profile.viewport_height},
                args=launch_args,
                accept_downloads=True,
            )
            logger.info(f"[{self.profile.name}] 持久化上下文创建成功")
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 启动失败: {e}，清理锁文件后重试...")
            self._kill_stale_browser_for_profile()
            self._cleanup_lock_files()
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                viewport={"width": self.profile.viewport_width, "height": self.profile.viewport_height},
                args=launch_args,
                accept_downloads=True,
            )
            logger.info(f"[{self.profile.name}] 持久化上下文创建成功（重试）")

        # 3.5 监听浏览器异常关闭（便于定位「一启动就被关」的根因）
        try:
            self._browser.on("close", lambda: logger.warning(
                f"[{self.profile.name}] 浏览器上下文已关闭（可能异常崩溃）"))
        except Exception:
            pass

        # 4. 初始化页面
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        # 5. 加载 cookies
        await self._load_cookies()

        logger.info(f"[{self.profile.name}] 浏览器启动完成")
    
    def _migrate_cookies_from_old_dir(self):
        """将旧 browser_data 目录的 deepseek cookies 迁移到新目录"""
        if self.platform_key != "deepseek":
            return  # 只对 deepseek 做迁移
        if not BROWSER_DATA_OLD.exists():
            return  # 旧目录不存在，跳过
        if not self.user_data_dir.exists():
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
        # 旧 cookies 文件路径
        old_cookie = BROWSER_DATA_OLD / "deepseek_cookies.json"
        new_cookie = self.user_data_dir / "cookies.json"
        if old_cookie.exists() and not new_cookie.exists():
            try:
                # 读取旧 cookies
                data = json.loads(old_cookie.read_text(encoding="utf-8"))
                # 转换为 cookies 列表格式（与 playwright 一致）
                with open(new_cookie, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"[{self.profile.name}] 已从旧目录迁移 {len(data)} 条 cookies")
            except Exception as e:
                logger.warning(f"[{self.profile.name}] cookies 迁移失败: {e}")
    
    def _cleanup_lock_files(self):
        """清理 Chromium 锁文件"""
        lock_files = ["SingletonLock", "SingletonCookieLock", "SingletonSocketLock", 
                       "SingletonPipeline", "Chrome_Port", "chrome_debug_port", 
                       "SingletonCookie", "lock.file"]
        for p in self.user_data_dir.iterdir():
            if any(x in p.name.lower() for x in lock_files):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
    
    async def _load_cookies(self):
        """加载 cookies"""
        cookie_file = self.user_data_dir / "cookies.json"
        if not cookie_file.exists():
            logger.info(f"[{self.profile.name}] 首次启动，无 cookies")
            return
        
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            if self._browser and cookies:
                await self._browser.add_cookies(cookies)
                logger.info(f"[{self.profile.name}] 已加载 {len(cookies)} 条 cookies")
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 加载 cookies 失败: {e}")
    
    async def save_cookies(self):
        """保存 cookies"""
        if not self._browser:
            return
        
        try:
            cookies = await self._browser.cookies()
            cookie_file = self.user_data_dir / "cookies.json"
            cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[{self.profile.name}] 已保存 {len(cookies)} 条 cookies")
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 保存 cookies 失败: {e}")
    
    async def navigate(self, url: str = None):
        """别名：与 BrowserManager 接口对齐，便于外部按 self.navigate() 调用。
        url 参数：若提供则导航到指定 URL；否则导航到本 profile 的 chat_url。"""
        if url:
            if not self._page:
                raise RuntimeError("页面未初始化")
            await self._page.goto(url, wait_until="commit", timeout=30000)
            logger.info(f"[{self.profile.name}] 已导航到 {url}")
        else:
            return await self.navigate_to_chat()

    async def wait_login(self, timeout: int = 180):
        """等待用户登录（与 BrowserManager 接口对齐）"""
        import time
        logger.info(f"[{self.profile.name}] 等待用户登录（最多 {timeout}s）...")
        start = time.time()
        while time.time() - start < timeout:
            if await self.check_login():
                await self.save_cookies()
                logger.info(f"[{self.profile.name}] 登录成功")
                return True
            await asyncio.sleep(3)
        logger.error(f"[{self.profile.name}] 登录超时")
        return False

    async def navigate_to_chat(self):
        """导航到聊天页面（自带自愈：若浏览器在导航时被杀，自动重启用，最多 3 次；
        最后 1 次用全新临时 profile 兜底，保证浏览器一定能开起来）"""
        if not self._page and self._browser is None:
            raise RuntimeError("浏览器未初始化")

        last_err = None
        for _attempt in range(3):
            try:
                # 浏览器已死（被关/崩溃）→ 强制重启
                if self._browser is None or (hasattr(self._browser, "is_closed")
                        and self._browser.is_closed()) or self._page is None:
                    if self._browser is not None:
                        try:
                            await self.close()
                        except Exception:
                            pass
                    self._browser = None
                    self._page = None
                    # 前两次复用原目录；最后一次用全新临时目录兜底
                    await self.launch(fresh_profile=(_attempt == 2))
                # 用 commit（收到响应即算成功，已实测 chat.qwen.ai 秒开）；
                # 不用 domcontentloaded：chat.qwen.ai 等 SPA 的 DOMContentLoaded
                # 在 goto 阶段迟迟不触发，会导致 30s 超时卡死。
                await self._page.goto(self.profile.chat_url,
                                     wait_until="commit", timeout=30000)
                # 软等 DOM 解析完成（最多 15s），失败不阻断——下游 send_message
                # 的 wait_for_selector 会兜底等输入框真正出现。
                try:
                    await self._page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                logger.info(f"[{self.profile.name}] 已导航到 {self.profile.chat_url}")
                return
            except Exception as e:
                last_err = e
                err = str(e)
                # 浏览器被关类错误 → 自愈重试
                if "has been closed" in err or "TargetClosed" in err or "Target page" in err:
                    logger.warning(
                        f"[{self.profile.name}] 导航时浏览器被杀，自愈重试 "
                        f"({_attempt + 1}/3)..."
                    )
                    try:
                        await self.close()
                    except Exception:
                        pass
                    self._browser = None
                    self._page = None
                    continue
                # 其它错误（URL 错误等）直接抛出
                raise
        if last_err:
            raise last_err

    async def check_login(self) -> bool:
        """检查登录状态（平台差异化处理）"""
        if not self._page:
            return False

        try:
            # 元宝专用逻辑：
            # 底部左侧有「未登录」头像/文字 = 未登录
            # 顶部右上角有「登录」按钮 = 未登录
            # class 含 nologin 的容器 = 未登录
            if self.profile.platform == "yuanbao":
                is_nologin = await self._page.evaluate("""() => {
                    // 1. 底部或左上角：未登录 + 用户头像位置
                    const bottomLogin = document.querySelector(
                        '[class*="bottom"], [class*="footer"] [class*="login"], ' +
                        '[class*="user"] [class*="login"], [class*="avatar"]'
                    );
                    if (bottomLogin && /未登录/.test(bottomLogin.innerText || '')) return true;

                    // 2. 页面主区域 nologin class
                    const containers = document.querySelectorAll('[class*="nologin"]');
                    for (const c of containers) {
                        if (c.offsetWidth > 0 && c.offsetHeight > 0) return true;
                    }

                    // 3. 右上角顶部导航栏「登录」按钮
                    const navLoginBtns = document.querySelectorAll('header [class*="login"], [class*="header"] [class*="login"], .yb-nav__user [class*="login"]');
                    for (const b of navLoginBtns) {
                        if (b.offsetWidth > 0 && b.offsetHeight > 0 && b.innerText.trim()) return true;
                    }

                    // 4. 页面 body 含明确的未登录提示
                    const bodyText = document.body.innerText || '';
                    if (/未登录/.test(bodyText) && !/登录/.test(bodyText.replace(/未登录/g, ''))) return true;
                    if (bodyText.includes('请登录') || bodyText.includes('微信扫码登录')) return true;

                    // 5. 没有 message 且 nologin class 存在
                    const hasMessage = document.querySelectorAll('[class*="message"], [class*="session"], [class*="dialog"]').length > 0;
                    const hasNologin = document.querySelectorAll('[class*="nologin"]').length > 0;
                    if (!hasMessage && hasNologin) return true;

                    // 6. 未登录时输入框 placeholder
                    const editor = document.querySelector('[contenteditable].ql-editor, textarea');
                    if (editor && /登录|请输入/.test(editor.getAttribute('placeholder') || '')) {
                        // 如果底部显示「未登录」也说明没登录
                        return false; // 先不据此判定
                    }

                    return false;
                }""")
                if is_nologin:
                    logger.info(f"[{self.profile.name}] check_login: 未登录")
                    return False

                logger.info(f"[{self.profile.name}] check_login: 已登录")
                return True

            # 其他平台的通用逻辑
            # 登录标记文字：默认「登录 / Login」，并叠加本平台 profile.login_texts
            # （用于识别 ChatGPT/Claude/Grok 等非中文登录入口）。
            login_texts = ["登录", "Login"] + list(self.profile.login_texts or [])
            parts = []
            for _t in login_texts:
                parts.append(f'button:has-text("{_t}")')
                parts.append(f'a:has-text("{_t}")')
            login_btn = self._page.locator(", ".join(parts))
            has_login = await login_btn.count() > 0

            # 如果找到了用户头像或消息历史，说明已登录
            has_content = False
            try:
                msg_count = await self._page.locator("[class*='message']").count()
                has_content = msg_count > 0
            except:
                pass

            logged_in = not has_login or has_content
            logger.info(f"[{self.profile.name}] 登录状态: {logged_in}")
            return logged_in
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 登录检查异常: {e}")
            return False
    
    async def wait_login(self, timeout: int = 180):
        """等待用户登录"""
        import time
        logger.info(f"[{self.profile.name}] 等待用户登录（最多 {timeout}s）...")
        
        start = time.time()
        while time.time() - start < timeout:
            if await self.check_login():
                await self.save_cookies()
                logger.info(f"[{self.profile.name}] 登录成功")
                return True
            await asyncio.sleep(3)
        
        logger.error(f"[{self.profile.name}] 登录超时")
        return False
    
    async def send_message(self, text: str, attachments: List[str] = None):
        """发送消息（含附件上传 + 发送后验证 + 诊断日志）

        attachments: 可选的文件路径列表（图片/PDF 等），发送前先上传到输入框。
        """
        if not self._page:
            raise RuntimeError("页面未初始化")

        # 确保已登录（关非登录弹窗 + 遇登录弹窗则等待用户登录）
        await self._ensure_logged_in_or_wait()

        # ── 附件上传（在填文本前先传文件，避免预览被清空）──
        if attachments:
            try:
                res = await self.upload_files(attachments)
                logger.info(f"[{self.profile.name}] 附件上传结果: {res}")
            except Exception as e:
                logger.warning(f"[{self.profile.name}] 附件上传失败: {e}")

        text_len = len(text)
        logger.info(f"[{self.profile.name}] send_message: 输入 {text_len} 字符")

        # 等待输入框出现
        try:
            await self._page.wait_for_selector(self.profile.input_selector, timeout=10000)
        except:
            logger.warning(f"[{self.profile.name}] 输入框选择器失败，尝试通用选择器")
            await self._page.wait_for_selector("textarea, [contenteditable='true']", timeout=10000)

        # 查找输入框
        input_el = None
        try:
            input_el = self._page.locator(self.profile.input_selector).first
            if await input_el.count() == 0:
                input_el = self._page.locator("textarea").first
        except:
            input_el = self._page.locator("textarea").first

        if not await input_el.is_visible():
            raise RuntimeError(f"{self.profile.name} 输入框不可见")

        # 清空并填写
        await input_el.click()
        await asyncio.sleep(0.2)
        await input_el.fill("")
        await asyncio.sleep(0.1)
        # 用 JS 直接设值（比 fill 快，尤其超长文本）
        try:
            await self._page.evaluate(
                "(el, txt) => { const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set; nativeInputValueSetter.call(el, txt); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }",
                input_el, text,
            )
        except Exception:
            # JS 设值失败 → 回退到 Playwright fill（慢但稳）
            await input_el.fill(text)
        await asyncio.sleep(0.3)

        # 发送前：记录当前消息数（用于验证发送成功）
        msg_count_before = 0
        try:
            msg_count_before = await self._page.evaluate(
                "(sel) => document.querySelectorAll(sel).length",
                self.profile.response_selector,
            ) or 0
        except Exception:
            pass

        # 发送
        sent = False
        if self.profile.send_selector:
            try:
                btn = self._page.locator(self.profile.send_selector).first
                if await btn.count() > 0 and await btn.is_enabled():
                    await btn.click()
                    logger.info(f"[{self.profile.name}] 已通过按钮发送")
                    sent = True
            except:
                pass

        if not sent:
            # 使用 Enter 键发送（对通义等平台，textarea 的 Enter 即发送）
            await input_el.press("Enter")
            logger.info(f"[{self.profile.name}] 已通过 Enter 键发送")

        # ── 发送后验证：等待新消息出现或输入框清空（证明消息确实发出去了）──
        verified = False
        for _verify_try in range(10):  # 最多等 5s
            await asyncio.sleep(0.5)
            try:
                # 方式1：检查消息数是否增加
                count_after = await self._page.evaluate(
                    "(sel) => document.querySelectorAll(sel).length",
                    self.profile.response_selector,
                ) or 0
                if count_after > msg_count_before:
                    verified = True
                    logger.info(f"[{self.profile.name}] 发送已验证（消息数 {msg_count_after} -> {count_after}）")
                    break
                # 方式2：检查输入框是否已清空
                val = await input_el.input_value() if hasattr(input_el, "input_value") else ""
                if not val.strip():
                    verified = True
                    logger.info(f"[{self.profile.name}] 发送已验证（输入框已清空）")
                    break
            except Exception:
                pass

        if not verified:
            logger.warning(
                f"[{self.profile.name}] ⚠️ 发送后 5s 内未检测到新消息/输入框未清空，"
                f"消息可能未成功发出（原文字数: {text_len}）"
            )
        return True
    
    # ============================================================
    # 深度思考开关（每个平台 UI 不同，逐个适配）
    # ============================================================
    async def enable_thinking(self, enable: bool) -> bool:
        """开启/关闭当前平台的「深度思考」模式。

        每个平台的开启方式不一样（DeepSeek 是输入框下方的 toggle 按钮；
        通义是输入框上方的模式开关；豆包/元宝是各自独立的思考模式入口），
        因此不能通吃单一选择器。按 profile.thinking_toggle_selectors 顺序尝试，
        对每个候选按钮检测激活状态后点击切换。全部失败则 dump 页面可点击元素，
        方便用户实跑时贴回实际 UI 让我精确修正。
        """
        if not self._page:
            return False
        # Qwen 等用下拉框（ant-select）选择思考模式 → 走 select 专属分支
        if (self.profile.thinking_mode or "toggle") == "select":
            return await self._enable_thinking_select(enable)
        # 豆包："深入研究" 按钮是 toolbar skill-item，点击前后 class/aria 不变，
        # 普通 _detect_toggle_active 无法判断状态，需要特殊处理。
        if self.profile.platform == "doubao":
            return await self._enable_thinking_doubao(enable)
        selectors = self.profile.thinking_toggle_selectors or []
        target = enable
        for sel in selectors:
            try:
                btns = self._page.locator(sel)
                n = await btns.count()
                for i in range(n):
                    btn = btns.nth(i)
                    try:
                        if not await btn.is_visible():
                            continue
                    except Exception:
                        continue
                    is_active = await self._detect_toggle_active(btn)
                    if is_active == target:
                        logger.info(f"[{self.profile.name}] 深度思考已是{'开启' if enable else '关闭'}")
                        return True
                    # 状态不符 → 点击切换
                    try:
                        await btn.click(timeout=3000)
                    except Exception as ce:
                        logger.warning(f"[{self.profile.name}] 点击 {sel} 失败: {ce}")
                        continue
                    await asyncio.sleep(0.8)
                    is_active2 = await self._detect_toggle_active(btn)
                    if is_active2 == target:
                        logger.info(f"[{self.profile.name}] 深度思考已{'开启' if enable else '关闭'} (selector={sel})")
                        return True
                    else:
                        logger.warning(
                            f"[{self.profile.name}] 点击 {sel} 后状态未变 "
                            f"({is_active}→{is_active2})，尝试下一个候选"
                        )
            except Exception as e:
                logger.warning(f"[{self.profile.name}] 尝试选择器 {sel} 失败: {e}")
                continue
        logger.warning(f"[{self.profile.name}] 未找到可用的深度思考开关，输出页面诊断")
        await self._diagnose_clickable(self.profile.name)
        return False

    async def _enable_thinking_select(self, enable: bool) -> bool:
        """针对 Ant Design 下拉框式「深度思考」选择器（如 Qwen）。

        流程：定位 .qwen-select-thinking（ant-select 根）→ 读当前选中项文字 →
        若已是目标态直接返回；否则点击 .ant-select-selector 展开下拉 →
        在选项里找「深度思考」（开启）或第一个非深度思考项（关闭）点击。
        """
        for sel in (self.profile.thinking_toggle_selectors or []):
            try:
                sel_el = self._page.locator(sel).first
                if await sel_el.count() == 0:
                    continue
                if not await sel_el.is_visible():
                    continue
                # 读当前选中项
                current = await self._read_select_value(sel_el)
                if enable and current and ("深度思考" in current or "深度" in current):
                    logger.info(f"[{self.profile.name}] 深度思考已开启（当前: {current}）")
                    return True
                if (not enable) and current and ("深度" not in current):
                    logger.info(f"[{self.profile.name}] 深度思考已关闭（当前: {current}）")
                    return True
                # 点击展开下拉
                await sel_el.click(timeout=3000)
                await asyncio.sleep(0.7)
                # 等待选项出现（ant-select 下拉渲染在 body 末尾）。
                # 注意：只能用 .ant-select-item-option（选项根），不能用 [class*='option']，
                # 否则会匹配到 option 内部的 state/content span（不可见、点击超时）。
                opt_loc = self._page.locator(
                    ".ant-select-item-option, li[role='option']"
                )
                try:
                    await opt_loc.first.wait_for(state="visible", timeout=3000)
                except Exception:
                    pass
                n = await opt_loc.count()
                chosen = None
                opts_text = []
                for i in range(n):
                    o = opt_loc.nth(i)
                    try:
                        t = (await o.inner_text()).strip()
                    except Exception:
                        t = ""
                    opts_text.append(t)
                logger.info(f"[{self.profile.name}] 深度思考下拉选项: {opts_text}")
                # 优先级：深度思考 > 深度/推理 > 思考（避免先匹配到「思考」而漏掉「深度思考」）
                if enable:
                    for kw in ("深度思考", "深度", "推理", "思考"):
                        for i in range(n):
                            if kw in opts_text[i]:
                                chosen = opt_loc.nth(i)
                                break
                        if chosen is not None:
                            break
                else:
                    # 关闭：选第一个不含「深度/推理/思考」的选项
                    for i in range(n):
                        if opts_text[i] and ("深度" not in opts_text[i]) and ("推理" not in opts_text[i]) and ("思考" not in opts_text[i]):
                            chosen = opt_loc.nth(i)
                            break
                if chosen is None and n > 0:
                    # 兜底前先打印所有选项，便于诊断实际文字
                    logger.warning(
                        f"[{self.profile.name}] 选项未匹配到关键词，实际选项: {opts_text}"
                    )
                    chosen = opt_loc.nth(n - 1 if enable else 0)
                if chosen is not None:
                    await chosen.click(timeout=3000)
                    await asyncio.sleep(0.8)
                    new_val = await self._read_select_value(sel_el)
                    logger.info(
                        f"[{self.profile.name}] 深度思考已{'开启' if enable else '关闭'}"
                        f"（selector={sel}, 选择: {new_val}）"
                    )
                    return True
                logger.warning(f"[{self.profile.name}] {sel} 展开后未找到选项")
            except Exception as e:
                logger.warning(f"[{self.profile.name}] select 模式尝试 {sel} 失败: {e}")
                continue
        logger.warning(f"[{self.profile.name}] 未找到深度思考下拉框，输出页面诊断")
        await self._diagnose_clickable(self.profile.name)
        return False

    async def _detect_login_modal(self) -> bool:
        """检测页面是否有登录弹窗（含二维码/扫码/微信登录等）。
        登录弹窗不能被自动关闭——用户需要扫码登录。"""
        if not self._page:
            return False
        try:
            found = await self._page.evaluate("""() => {
                const containers = document.querySelectorAll(
                    '.semi-modal-wrap, .ant-modal-wrap, .ant-modal, ' +
                    '[class*="login-modal"], [class*="loginMask"], [class*="loginDialog"], ' +
                    '[class*="login-mask"], [class*="LoginModal"], [class*="qr-login"], ' +
                    '[class*="qrcode"], [class*="QRCode"], [class*="loginContainer"]'
                );
                for (const c of containers) {
                    if (c.offsetWidth === 0 && c.offsetHeight === 0) continue;
                    const html = (c.innerHTML || '').toLowerCase();
                    // 登录弹窗文字特征
                    if (html.includes('扫码') || html.includes('二维码') ||
                        html.includes('qrcode') || html.includes('qr-code') ||
                        html.includes('微信登录') || html.includes('微信扫码') ||
                        html.includes('手机号登录') || html.includes('手机扫码') ||
                        html.includes('扫码登录') || html.includes('登录后')) {
                        return true;
                    }
                    // 含 canvas 二维码 + 登录关键词的弹窗
                    if (c.querySelector('canvas') && html.includes('登录')) return true;
                    // 含疑似二维码图片
                    if (c.querySelector('img[src*="qr"], img[src*="code"], img[src*="Qr"], img[src*="scan"]')) return true;
                }
                return false;
            }""")
            return bool(found)
        except Exception:
            return False

    async def _ensure_logged_in_or_wait(self) -> bool:
        """确保已登录：先关非登录弹窗，若检测到登录弹窗则等待用户登录完成。
        这是 send_message / upload_files / enable_thinking 等操作的前置守卫，
        避免登录弹窗被误关导致用户永远没机会扫码登录。"""
        await self._dismiss_login_modal()
        if await self._detect_login_modal():
            logger.info(f"[{self.profile.name}] 检测到登录弹窗，等待用户登录（最多 300s）…")
            ok = await self.wait_login(timeout=300)
            if ok:
                logger.info(f"[{self.profile.name}] 登录成功，继续操作")
                await self._dismiss_login_modal()
            return ok
        return True

    async def _dismiss_login_modal(self) -> bool:
        """关闭非登录类弹窗（引导/通知/cookie 提示等），避免 pointer-events 拦截。
        ⚠️ 登录弹窗（含二维码/扫码）不会被关闭——用户需要扫码登录。"""
        if not self._page:
            return False
        # 登录弹窗不关——给用户登录的机会
        if await self._detect_login_modal():
            logger.info(f"[{self.profile.name}] 检测到登录弹窗，不自动关闭（需用户登录）")
            return False
        closed = False
        # 1) 按 Escape（只对非登录弹窗）
        try:
            await self._page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass
        # 2) 常见关闭选择器（豆包 semi-design / 元宝 / antd）
        for sel in [
            '.semi-modal-close', '.semi-modal-close-x', '.semi-modal-close-btn',
            '[class*="semi-modal"] [class*="close"]', '[class*="semi-modal-header"] [class*="close"]',
            '.ant-modal-close', '.ant-modal-close-x', '.ant-modal-close-icon',
            '[class*="login-modal"] [class*="close"]', '[class*="login-popup"] [class*="close"]',
            'button:has-text("✕")', 'button:has-text("×")',
            '[aria-label*="close" i]', '[aria-label*="关闭" i]',
        ]:
            try:
                loc = self._page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=3000)
                    closed = True
                    await asyncio.sleep(0.5)
            except Exception:
                continue
        # 3) JS 兜底：找 modal 容器，找内部含 close/× 的元素点击
        try:
            clicked = await self._page.evaluate("""() => {
                const modals = document.querySelectorAll('.semi-modal-wrap, .ant-modal-wrap, .ant-modal, [class*="login-modal"], [class*="loginMask"], [class*="loginDialog"]');
                for (const m of modals) {
                    if (m.offsetWidth === 0) continue;
                    const closeBtns = m.querySelectorAll('[class*="close"], [class*="Close"], button');
                    for (const b of closeBtns) {
                        const t = (b.innerText || '').trim();
                        if (t === '✕' || t === '×' || t === 'X' || t === 'x') {
                            b.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                closed = True
                await asyncio.sleep(0.5)
        except Exception:
            pass
        return closed

    async def _enable_thinking_doubao(self, enable: bool) -> bool:
        """豆包特殊处理：'深入研究' 是 toolbar 里的 skill-item，点击前后 class 不变，
        通过分别点击 '深入研究'（开启）和 '快速'（关闭）来切模式。"""
        if not self._page:
            return False
        await self._ensure_logged_in_or_wait()
        target_btn = "深入研究" if enable else "快速"
        opposite = "快速" if enable else "深入研究"
        try:
            # 优先点目标按钮
            btn = self._page.locator(f"button:has-text('{target_btn}')").first
            if await btn.count() == 0 or not await btn.is_visible():
                logger.warning(f"[豆包] 未找到 '{target_btn}' 按钮")
                return False
            await btn.click(timeout=5000)
            await asyncio.sleep(0.8)
            logger.info(f"[豆包] 已点击 '{target_btn}'（意图：{'开启' if enable else '关闭'}深度思考）")
            return True
        except Exception as e:
            logger.warning(f"[豆包] 点击 '{target_btn}' 失败: {e}，尝试用 force 点击")
            try:
                btn = self._page.locator(f"button:has-text('{target_btn}')").first
                await btn.click(timeout=5000, force=True)
                await asyncio.sleep(0.8)
                logger.info(f"[豆包] 已 force 点击 '{target_btn}'")
                return True
            except Exception as e2:
                logger.warning(f"[豆包] force 点击也失败: {e2}")
                return False

    @staticmethod
    async def _read_select_value(el) -> str:
        """读取 ant-select 当前选中的文字（.ant-select-selection-item）。"""
        try:
            return await el.evaluate("""el => {
                const item = el.querySelector('.ant-select-selection-item')
                    || el.querySelector('.ant-select-selection-search-input');
                if (item) return (item.innerText || item.value || '').trim();
                return (el.innerText || '').trim();
            }""")
        except Exception:
            return ""

    @staticmethod
    async def _detect_toggle_active(btn) -> Optional[bool]:
        """检测 toggle 按钮是否激活。返回 True/False/None（无法判断）。

        注意：深色主题下透明背景 `rgba(0,0,0,0)` 会被 `bg.includes('rgb(0,')` 误判；
        常见类名里的 `button`/`icon` 等包含 `on`，因此必须用独立词边界匹配。
        """
        try:
            info = await btn.evaluate("""el => {
                const cls = (' ' + (el.className || '').toString() + ' ').toLowerCase();
                const ariaPressed = el.getAttribute('aria-pressed');
                const ariaExpanded = el.getAttribute('aria-expanded');
                const ariaChecked = el.getAttribute('aria-checked');
                const hasWord = (w) => new RegExp('(?:^|[-_\\s])' + w + '(?:[-_\\s]|$)').test(cls);
                const hasActive = hasWord('active') || hasWord('selected') || hasWord('enabled')
                    || hasWord('checked') || hasWord('thinking') || hasWord('deep-research')
                    || hasWord('deepresearch') || hasWord('on');
                const computed = window.getComputedStyle(el);
                const bg = computed.backgroundColor || '';
                const isTransparentOrNeutral = (s) => {
                    return s.includes('rgba(0, 0, 0, 0') || s === 'rgb(0, 0, 0)'
                        || s === 'rgb(255, 255, 255)' || s === 'transparent';
                };
                const isBrandColor = (s) => {
                    const low = s.toLowerCase();
                    return low.includes('blue') || low.includes('purple')
                        || low.includes('#18') || low.includes('rgb(59, 130, 246')
                        || low.includes('rgb(37, 99, 235') || low.includes('rgb(99, 102, 241');
                };
                const isColored = !isTransparentOrNeutral(bg) && isBrandColor(bg);
                return { hasActive, ariaPressed, ariaExpanded, ariaChecked, isColored, bg };
            }""")
            if info.get("ariaPressed") == "true": return True
            if info.get("ariaPressed") == "false": return False
            if info.get("ariaExpanded") == "true": return True
            if info.get("ariaExpanded") == "false": return False
            if info.get("ariaChecked") == "true": return True
            if info.get("ariaChecked") == "false": return False
            if info.get("hasActive"): return True
            if info.get("isColored"): return True
            return False
        except Exception:
            return None

    async def _diagnose_clickable(self, tag: str):
        """诊断：dump 页面上所有可点击元素文字，方便用户贴回实际 UI 修正选择器。"""
        try:
            items = await self._page.evaluate("""() => {
                const out = [];
                const sels = ['button', 'div[role=button]', 'label', 'a', '[class*=switch]', '[class*=toggle]'];
                for (const s of sels) {
                    for (const el of document.querySelectorAll(s)) {
                        const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                        if (t && t.length < 30) out.push(t);
                    }
                }
                return [...new Set(out)].slice(0, 60);
            }""")
            logger.warning(
                f"[{tag}] 页面可点击元素（请贴回实际 UI 让我精确修正选择器）:\n"
                + "\n".join(f"  - {x}" for x in (items or []))
            )
        except Exception:
            pass

    # ============================================================
    # 文件上传（图片 / PDF 等，支持多文件）
    # ============================================================
    async def upload_files(self, file_paths: List[str]) -> str:
        """上传一个或多个文件（图片/PDF 等）到当前聊天输入框。

        千问 chat.qwen.ai 的特殊处理（关键修复）：千问的隐藏 #filesUpload 必须先
        在输入框左侧点「+」(选择模式) 按钮、再点下拉菜单里的「上传附件」项，React
        才会真正接管 file input；直接对 #filesUpload 调 set_input_files 千问前端
        收不到，表现为「假上传」。故支持 attach_menu_item_text 两步流程，并在设值后
        做真实校验（页面出现文件名/预览），杜绝静默假成功。

        稳定性策略：走「+ -> 上传附件」菜单触发原生文件框，用 expect_file_chooser
        正式接管注入（这是有头模式下不弹系统框的唯一可靠办法）；chooser 未触发时
        退回「force 点击武装 input + set_input_files」；每次注入后都做真实校验，
        整体最多重试 3 轮，确保千问上传是真上传而非假成功。
        """
        if not self._page:
            raise RuntimeError("页面未初始化")

        # 确保已登录（关非登录弹窗 + 遇登录弹窗则等待用户登录）
        await self._ensure_logged_in_or_wait()

        valid = []
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                logger.warning(f"[{self.profile.name}] 文件不存在，跳过: {fp}")
                continue
            valid.append(str(p))
        if not valid:
            return "错误：没有有效文件可上传（路径不存在）"

        names = ", ".join(Path(v).name for v in valid)
        file_loc = self._page.locator("input#filesUpload, input[type='file']")

        async def _verify_uploaded() -> bool:
            """真实校验：页面是否出现刚上传文件的预览/文件名（千问会显示『解析中……』）。
            注意：千问把文件名显示成带换行的形式（如 _name 换行 .pdf），故页面与文件名
            都要先去掉所有空白再比对，否则会误判『未挂载』导致假失败。"""
            try:
                return await self._page.evaluate("""(fnames) => {
                    const clean = s => (s || '').toLowerCase()
                        .split(' ').join('')
                        .split(String.fromCharCode(10)).join('')
                        .split(String.fromCharCode(9)).join('')
                        .split(String.fromCharCode(13)).join('');
                    const norm = fnames.map(clean);
                    const body = clean(document.body.innerText);
                    for (const n of norm) if (body.includes(n)) return true;
                    const els = [...document.querySelectorAll('*')];
                    for (const el of els) {
                        const t = clean(el.innerText);
                        for (const n of norm) if (t.includes(n)) return true;
                    }
                    return false;
                }""", [Path(v).name for v in valid])
            except Exception:
                return False

        async def _inject_via_menu() -> bool:
            """两步流程：点 + -> 点上传附件 -> (chooser 接管 | 武装后 set_input_files) -> 校验。
            返回 True 表示文件已真正挂载到输入框。"""
            if not self.profile.attach_button_selector:
                return False
            sels = [s.strip() for s in self.profile.attach_button_selector.split(",") if s.strip()]
            for sel in sels:
                btn = self._page.locator(sel).first
                if await btn.count() == 0 or not await btn.is_visible():
                    continue
                try:
                    await btn.click(timeout=5000)
                except Exception:
                    await btn.click(timeout=5000, force=True)
                await asyncio.sleep(1.0)
                if not self.profile.attach_menu_item_text:
                    # 无菜单项：点开后直接注入已出现的 input
                    fi = file_loc.first
                    if await fi.count() > 0:
                        await fi.set_input_files(valid[0] if len(valid) == 1 else valid)
                        await asyncio.sleep(2.0)
                        return await _verify_uploaded()
                    return False
                mi = self._page.locator(
                    ".qwen-chat-v2-dropdown-menu-item:has-text('" + self.profile.attach_menu_item_text + "')").first
                # 轮询菜单项渲染（最多 ~6s），确保下拉菜单真正出现
                for _ in range(20):
                    await asyncio.sleep(0.3)
                    if await mi.count() > 0 and await mi.is_visible():
                        break
                else:
                    return False
                # 点「上传附件」触发原生文件框，用 chooser 正式接管注入
                try:
                    async with self._page.expect_file_chooser(timeout=8000) as fc_info:
                        await mi.click(timeout=5000)
                    fc = await fc_info.value
                    await fc.set_files(valid[0] if len(valid) == 1 else valid)
                except Exception:
                    # chooser 未触发：force 点击武装 input，再 set_input_files 兜底
                    try:
                        await mi.click(timeout=5000, force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)
                    fi = file_loc.first
                    if await fi.count() > 0:
                        await fi.set_input_files(valid[0] if len(valid) == 1 else valid)
                await asyncio.sleep(2.5)
                if await _verify_uploaded():
                    return True
                # 校验未过：再补一次 set_input_files（应对 chooser 注入但 React 未刷新）
                fi = file_loc.first
                if await fi.count() > 0:
                    try:
                        await fi.set_input_files(valid[0] if len(valid) == 1 else valid)
                    except Exception:
                        pass
                    await asyncio.sleep(2.5)
                    return await _verify_uploaded()
                return await _verify_uploaded()
            return False

        # 无 attach_button 平台（deepseek 等）：直接 set_input_files 即可
        if not self.profile.attach_button_selector:
            fi = file_loc.first
            if await fi.count() == 0:
                logger.warning(f"[{self.profile.name}] 未找到文件上传入口，诊断页面...")
                await self._diagnose_clickable(self.profile.name)
                return "错误：未找到文件上传入口（该平台可能不支持，或需登录后才有）"
            try:
                await fi.set_input_files(valid[0] if len(valid) == 1 else valid)
                await asyncio.sleep(2.0)
            except Exception as e:
                logger.error(f"[{self.profile.name}] 文件上传失败: {e}")
                return f"文件上传失败: {e}"
            if await _verify_uploaded():
                logger.info(f"[{self.profile.name}] 已上传 {len(valid)} 个文件: {names}（已校验）")
                return f"已上传 {len(valid)} 个文件: {names}"
            return f"错误：文件未能真正挂载到输入框；{names}"

        # 千问等两步流程平台：最多重试 3 轮，杜绝不稳定导致的假失败
        for attempt in range(3):
            if await _inject_via_menu():
                logger.info(f"[{self.profile.name}] 已上传 {len(valid)} 个文件: {names}（两步流程校验通过）")
                return f"已上传 {len(valid)} 个文件: {names}"
            # 重置：关闭可能残留的菜单，准备下一轮重试
            try:
                await self._page.keyboard.press("Escape")
            except Exception:
                pass
            await asyncio.sleep(0.8)
        return f"错误：文件未能真正挂载到输入框（千问可能需要先点『+』->『上传附件』；{names}）"

    async def upload_file(self, file_path: str):
        """上传单个文件（兼容旧接口）"""
        return await self.upload_files([file_path])
    
    async def _read_thinking(self, selector: str) -> str:
        """读取当前页面最长的「思考过程」文本"""
        if not selector or not self._page:
            return ""
        try:
            return await self._page.evaluate(
                "(sel) => {"
                " const els = document.querySelectorAll(sel);"
                " let best = '';"
                " for (const el of els) {"
                "   const t = (el.innerText || '').trim();"
                "   if (t && t.length > best.length) best = t;"
                " }"
                " return best;"
                " }",
                selector,
            )
        except Exception:
            return ""

    async def _read_last_reply(self, baseline_count: int) -> dict:
        """读取当前页面最新消息的完整文本，以及消息总数。

        关键修复（解决「网页自带文字污染上下文」）：

        问题：chat.qwen.ai 等站点会把网页自带的 UI 文字渲染进匹配选择器的
        容器里，例如输入框旁的「快速回答」开关、回复底部的免责声明
        「人工智能生成的内容可能不准确」、以及「复制 / 赞 / 踩 / 重新生成」
        按钮。旧实现把这些 innerText 原样读出来当成 AI 回复 → 存进
        self._messages['assistant'] → 下一轮又作为上下文发给模型 → 模型被
        「快速快速快速」「人工智能生成的内容可能不准确」淹没、完全混乱。

        新策略：对每个匹配元素的文本做「逐行清洗」——
          - 整行等于某个 UI 噪音词（如「快速」「人工智能生成的内容可能不准确」）⇒ 丢弃该行；
          - 整行去掉所有 UI 噪音词后变空（如「快速快速快速」）⇒ 丢弃该行；
          - 其余行原样保留（不破坏正文里合法出现的「快速」等词）。
        清洗后若整个元素文本为空，说明它是纯 UI 元素（开关/按钮），直接跳过。

        另外仍保留：收集 baseline 之后所有新增元素并拼接，避免一条消息被
        拆成多个 DOM 子节点时只拿到半截（解决「qwen 工具调用丢失」）。
        """
        # 注意：response_selector 内含 [class*='message'] 这类带单引号的选择器，
        # 不能再用 f-string 注入到 JS 单引号字符串里（会导致 JS 语法错误、evaluate 抛异常、
        # 进而读取回复永远返回空）。正确做法：把选择器作为 evaluate 的参数传入。
        # 另外 Playwright 的 page.evaluate 只接受【一个】arg，多值要用数组传入再解构。
        sel = self.profile.response_selector
        return await self._page.evaluate(
            "(args) => {"
            " const sel = args[0]; const base = args[1]; const noise = args[2];"
            " const cleanPart = (raw) => {"
            "   const lines = (raw || '').split(/\\r?\\n/);"
            "   const kept = [];"
            "   for (const line of lines) {"
            "     const s = line.trim();"
            "     if (!s) continue;"
            "     let isUi = false;"
            "     for (const n of noise) { if (s === n) { isUi = true; break; } }"
            "     if (isUi) continue;"
            "     let stripped = s;"
            "     for (const n of noise) { stripped = stripped.split(n).join(''); }"
            "     if (stripped.trim().length === 0) continue;"
            "     kept.push(line);"
            "   }"
            "   return kept.join('\\n').trim();"
            " };"
            " const els = document.querySelectorAll(sel);"
            " const count = els.length;"
            " let text = '';"
            " if (count > base) {"
            "   const parts = [];"
            "   for (let i = base; i < count; i++) {"
            "     const el = els[i];"
            "     const tag = el.tagName;"
            "     if (tag === 'TEXTAREA' || tag === 'INPUT') continue;"
            "     if (el.getAttribute && el.getAttribute('contenteditable') !== null) continue;"
            "     const t = (el.innerText || '').trim();"
            "     if (!t) continue;"
            "     const cleaned = cleanPart(t);"
            "     if (!cleaned) continue;"
            "     parts.push(cleaned);"
            "   }"
            "   text = parts.join('\\n\\n');"
            " } else {"
            "   for (let i = els.length - 1; i >= 0; i--) {"
            "     const el = els[i];"
            "     const tag = el.tagName;"
            "     if (tag === 'TEXTAREA' || tag === 'INPUT') continue;"
            "     if (el.getAttribute && el.getAttribute('contenteditable') !== null) continue;"
            "     const t = (el.innerText || '').trim();"
            "     if (!t) continue;"
            "     const cleaned = cleanPart(t);"
            "     if (!cleaned) continue;"
            "     text = cleaned; break;"
            "   }"
            " }"
            " return { count: count, text: text, newAppeared: count > base };"
            " }",
            [sel, baseline_count, UI_NOISE_TOKENS],
        )

    async def wait_response(self, timeout: int = 60, on_thinking=None,
                            thinking_selector: str = "") -> Optional[str]:
        """等待 AI 回复（可选实时抓取「思考过程」并通过 on_thinking 回调上报）

        核心修复（解决「靠等待时间判断 → qwen 交互提前结束」）：

        旧逻辑用「文本连续 N 秒不变 = 稳定」+ 10s 冷却期判定完成，导致 qwen 在
        「先文字后 @@@@ 工具调用」的间隔里被误判完成而提前返回。

        新逻辑【以浏览器 UI 信号为准】（与人工盯界面一致）：
        - 用平台专属 generating_selector 检测「AI 是否还在生成」：
            * qwen/tongyi: ".stop-button" —— 生成时出现，完成后被移除（换成 .send-button）
            * deepseek:    ".loading, [class*='generating']"
          该元素在生成期间「存在且可见」，生成完成即消失。
        - 只有「回复文本已出现 + 生成信号消失 + 文本稳定 + 内容完整」才判定完成。
        - 不再依赖固定冷却期：模型生成多久就等多久，UI 说停才停。
        - _looks_incomplete 仍作内容完整性兜底（防止在工具调用写到一半时返回）。
        - 若某平台未配置 generating_selector，则回落到「文本稳定」兜底。
        """
        if not self._page:
            return None

        last_thinking = ""
        best_text = ""
        stable_count = 0
        gen_sel = self.profile.generating_selector or ""
        thinking_sel = thinking_selector or self.profile.thinking_selector
        text = ""
        appeared = False
        total_count = 0  # 诊断：页面上当前消息总数
        _diag_tick = 0  # 诊断：每 20 次 tick 打印一次状态

        # 基线：发送前的消息数（必须在引用前完成赋值，否则 UnboundLocalError）
        baseline = 0
        try:
            baseline = await self._page.evaluate(
                "(sel) => document.querySelectorAll(sel).length",
                self.profile.response_selector,
            ) or 0
        except Exception:
            baseline = 0

        logger.info(f"[{self.profile.name}] 等待回复（UI 信号驱动，{timeout}s 上限）... baseline_msg={baseline}")

        interval = 0.5
        n_ticks = int(timeout / interval) + 1
        gen_js = (
            "(sel) => { const els = document.querySelectorAll(sel);"
            " for (const el of els) { const r = el.getBoundingClientRect();"
            " if (r.width > 0 && r.height > 0) return true; } return false; }"
        )

        for _ in range(n_ticks):
            await asyncio.sleep(interval)
            _diag_tick += 1
            try:
                info = await self._read_last_reply(baseline)
                if not isinstance(info, dict):
                    # 诊断：_read_last_reply 返回非 dict（可能是异常被吞掉）
                    if _diag_tick % 20 == 1:
                        logger.warning(
                            f"[{self.profile.name}] wait_response tick={_diag_tick}: "
                            f"_read_last_reply 返回 {type(info).__name__}（非 dict），"
                            f"best={len(best_text)}字 appeared={appeared}"
                        )
                    continue
                text = info.get("text") or ""
                appeared = info.get("newAppeared", False)
                total_count = info.get("count", 0)

                # 持续记录最新文本（取最长，避免漏掉尾部流式片段）
                if appeared and text and len(text) > len(best_text):
                    best_text = text
                    stable_count = 0

                # 还没出字（含「提交中、尚未首个 token」的空窗）→ 视为仍在生成，继续等
                if not appeared or not text:
                    stable_count = 0
                    continue

                # UI 生成信号：该元素可见 ⇒ AI 还在输出
                generating = None
                if gen_sel:
                    try:
                        generating = await self._page.evaluate(gen_js, gen_sel)
                    except Exception as e:
                        # 诊断：generating_selector JS 执行失败（选择器可能不匹配当前 DOM）
                        if _diag_tick % 20 == 1:
                            logger.warning(
                                f"[{self.profile.name}] wait_response: "
                                f"generating_selector('{gen_sel}') 执行异常: {str(e)[:60]}"
                            )
                        generating = None

                if generating is True:
                    stable_count = 0
                    if _diag_tick % 20 == 1:
                        logger.info(
                            f"[{self.profile.name}] tick={_diag_tick}: 仍在生成中，"
                            f"已捕获 {len(best_text)}字 total_msg={total_count}"
                        )
                    continue

                # 文本未增长（已稳定）且内容完整 ⇒ 完成
                if best_text and len(text) == len(best_text):
                    stable_count += 1
                else:
                    stable_count = 0

                # UI 信号模式下：稳定 ~1.5s 即判定完成（等尾部流式落定）
                if generating is False and stable_count >= 3:
                    if self._looks_incomplete(best_text):
                        logger.info(
                            f"[{self.profile.name}] 文本疑似未完整（{len(best_text)}字），继续等待"
                        )
                        stable_count = 0
                        continue
                    logger.info(f"[{self.profile.name}] 回复完成（UI 信号）: {len(best_text)} 字")
                    if on_thinking and last_thinking:
                        try:
                            on_thinking(last_thinking)
                        except Exception:
                            pass
                    return best_text

                # 兜底（无 generating_selector 时）：文本稳定 ~3s 判定完成
                if generating is None and stable_count >= 6:
                    if self._looks_incomplete(best_text):
                        stable_count = 0
                        continue
                    logger.info(f"[{self.profile.name}] 回复完成（稳定兜底）: {len(best_text)} 字")
                    if on_thinking and last_thinking:
                        try:
                            on_thinking(last_thinking)
                        except Exception:
                            pass
                    return best_text
            except Exception:
                pass

            # 实时抓取思考过程（与回复轮询并行）
            if thinking_sel and on_thinking:
                try:
                    t = await self._read_thinking(thinking_sel)
                    if t and t != last_thinking:
                        last_thinking = t
                        try:
                            on_thinking(t)
                        except Exception:
                            pass
                except Exception:
                    pass

        # 超时兜底
        logger.warning(
            f"[{self.profile.name}] 等待回复超时（{timeout}s/{n_ticks} ticks），"
            f"已捕获 {len(best_text)} 字, appeared={appeared}, page_msg≈{total_count}"
        )
        if on_thinking and last_thinking:
            try:
                on_thinking(last_thinking)
            except Exception:
                pass
        return best_text if best_text else None

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        """检查文本是否看起来还未输出完毕（防止在工具调用写到一半时返回）。

        覆盖场景：
        - qwen 正在写 @@@@ { "tool": ... 但还没写完闭合的 @@@@
        - JSON 大括号/方括号未配对（说明代码块还在流式输出）
        - 文本以常见「正在输入」标记结尾
        """
        if not text:
            return False
        stripped = text.rstrip()

        # 1. 未闭合的 @@@@ 协议块（最关键：qwen 工具调用格式）
        open_marks = stripped.count("@@@@")
        if open_marks % 2 == 1:          # 奇数个 @@@@ = 有未闭合的协议块
            return True
        # 也检查只剩半个的情况（如 "@@@" 在末尾但后面没有内容）
        if stripped.endswith("@@@") and not stripped.endswith("@@@@@@"):
            # 以恰好 3 个 @ 结尾，可能是打开标记
            return True

        # 2. 未闭合的 JSON（大括号/方括号不配对）
        # 只检查最后 200 字符（避免全文统计开销），且只在不包含完整闭合对时报警
        tail = stripped[-200:] if len(stripped) > 200 else stripped
        brace_diff = tail.count("{") - tail.count("}")
        bracket_diff = tail.count("[") - tail.count("]")
        if brace_diff > 0 or bracket_diff > 0:
            return True

        # 3. 常见 AI 「光标/输入中」标记
        for marker in ("▌", "◌", "█", "…", "...", "⟳", "⏳"):
            if stripped.endswith(marker):
                return True

        return False
    
    async def new_conversation(self):
        """新建对话"""
        if not self._page:
            raise RuntimeError("页面未初始化")
        
        try:
            # 尝试多种新对话按钮选择器
            selectors = [
                "a:has-text('新对话')",
                "button:has-text('New Chat')",
                "[data-testid='new-chat']",
                "a[href='/']",
            ]
            
            for sel in selectors:
                try:
                    btn = self._page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click()
                        await asyncio.sleep(2)
                        logger.info(f"[{self.profile.name}] 新对话已创建")
                        return
                except:
                    continue
            
            logger.warning(f"[{self.profile.name}] 未找到新对话按钮")
        except Exception as e:
            logger.error(f"[{self.profile.name}] 新建对话失败: {e}")
    
    async def screenshot(self, path: str):
        """截图"""
        if not self._page:
            raise RuntimeError("页面未初始化")
        await self._page.screenshot(path=path, full_page=True)
        logger.info(f"[{self.profile.name}] 截图已保存: {path}")
    
    async def close(self):
        """关闭浏览器"""
        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
        
        self._browser = None
        self._page = None
        self._playwright = None
        logger.info(f"[{self.profile.name}] 浏览器已关闭")


class MultiPlatformManager:
    """
    多平台管理器
    
    管理多个独立浏览器的生命周期。
    每个平台有独立的 Chromium 进程和独立的 cookies 文件。
    """
    
    def __init__(self):
        self._browsers: Dict[str, PlatformBrowserManager] = {}
    
    def get(self, platform_key: str) -> Optional[PlatformBrowserManager]:
        """获取指定平台的浏览器管理器"""
        return self._browsers.get(platform_key)
    
    def add(self, platform_key: str, headless: bool = False):
        """添加平台浏览器"""
        if platform_key not in self._browsers:
            self._browsers[platform_key] = PlatformBrowserManager(platform_key, headless)
            logger.info(f"已添加平台: {platform_key}")
    
    async def launch_all(self):
        """启动所有平台浏览器"""
        tasks = []
        for key, browser in self._browsers.items():
            tasks.append(browser.launch())
        
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"已启动 {len(self._browsers)} 个平台浏览器")
    
    async def check_login_all(self) -> Dict[str, bool]:
        """检查所有平台登录状态"""
        results = {}
        for key, browser in self._browsers.items():
            try:
                logged_in = await browser.check_login()
                results[key] = logged_in
            except:
                results[key] = False
        
        for key, status in results.items():
            logger.info(f"[{key}] 登录状态: {'已登录' if status else '未登录'}")
        
        return results
    
    async def navigate_all(self):
        """导航所有平台到聊天页面"""
        tasks = []
        for key, browser in self._browsers.items():
            tasks.append(browser.navigate_to_chat())
        
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("所有平台已导航到聊天页面")
    
    async def wait_login_all(self, timeout: int = 180):
        """等待所有平台登录"""
        tasks = []
        for key, browser in self._browsers.items():
            tasks.append(browser.wait_login(timeout=timeout))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return dict(zip(self._browsers.keys(), results))
    
    async def close_all(self):
        """关闭所有平台浏览器"""
        for key, browser in self._browsers.items():
            await browser.close()
        
        self._browsers.clear()
        logger.info("所有平台浏览器已关闭")
    
    @property
    def browser_count(self) -> int:
        return len(self._browsers)


# ============================================================
# 通用平台会话（让 Commander 可在任意平台上对话）
# ============================================================

class PlatformSession:
    """
    通用平台会话适配器。

    实现与 DeepSeekSession 相同的对外接口（send / set_system_prompt /
    toggle_thinking / thinking_mode / set_deep_think / initialize），
    这样 Commander 无需改动即可把 `_session` 切换到任意平台。

    内部通过 PlatformBrowserManager 的 send_message + wait_response 完成对话。
    """

    def __init__(self, platform_bm: "PlatformBrowserManager"):
        self._bm = platform_bm
        self._messages: list = []          # [{"role":..,"content":..}]
        self._system_prompt: str = ""
        self._thinking_mode = False
        self._logged_in = True             # 由外部 check_login 决定，这里默认放行
        self._on_event = None              # 思考过程等事件回调 (event_type, data) -> None
        self._last_thinking = ""

    # ---- 与 DeepSeekSession 对齐的属性/方法 ----
    @property
    def thinking_mode(self) -> bool:
        return self._thinking_mode

    def toggle_thinking(self):
        self._thinking_mode = not self._thinking_mode
        logger.info(f"[{self._bm.profile.name}] 思考模式: {self._thinking_mode}")

    async def set_deep_think(self, enable: bool):
        """开启/关闭深度思考：实际点击平台 UI 的「深度思考」开关（不再只是设内存标志）"""
        self._thinking_mode = enable
        try:
            ok = await self._bm.enable_thinking(enable)
            if ok:
                logger.info(f"[{self._bm.profile.name}] 深度思考已{'开启' if enable else '关闭'}")
            else:
                logger.warning(
                    f"[{self._bm.profile.name}] 深度思考按钮未找到"
                    f"（可能该平台不支持，或需登录后才有；已 dump 页面元素供修正）"
                )
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 深度思考切换失败: {e}")

    async def initialize(self):
        # 浏览器已在切换时 launch + navigate，这里无需重复
        return

    def set_system_prompt(self, system_prompt: str):
        self._system_prompt = system_prompt or ""

    def set_on_event(self, cb):
        """设置事件回调，用于把「思考过程」等事件推给 GUI（参数: event_type, data）"""
        self._on_event = cb

    def _emit_thinking(self, text: str):
        self._last_thinking = text
        if self._on_event:
            try:
                self._on_event("ai_thinking", {"text": text})
            except Exception:
                pass

    def _build_context(self, new_user_text: str) -> str:
        """把历史 + 新消息拼成单条文本发送给平台

        【关键】系统提示词（===核心指令=== / @@@@ 协议 / 工具列表 / 规则）必须
        随每轮『反复』发送给模型——这是操作协议正常运作的前提：模型只有每次都
        看到协议，才知道用 @@@@ JSON 格式回指令、才知道有哪些工具可用、才能
        用工具控制电脑。之前错误地把系统提示词从上下文剔除，导致模型根本不知道
        协议、只能乱回。

        与 DeepSeekSession 保持一致：助手消息上限 4000 字符、最近 6 条不截断，
        避免多轮后上下文被腰斩导致「历史记录是坏的」。
        """
        keep_full = 6
        start_full = max(0, len(self._messages) - keep_full)
        lines = []
        # 系统提示词（协议）每轮都放在最前面反复喂给模型
        if self._system_prompt:
            lines.append(f"[系统指令]\n{self._system_prompt}")
        for idx, m in enumerate(self._messages):
            if m["role"] == "user":
                lines.append(f"[用户] {m['content']}")
            elif m["role"] == "assistant":
                content = m["content"] if idx >= start_full else m["content"][:4000]
                lines.append(f"[助手] {content}")
        lines.append(f"[用户] {new_user_text}")
        return "\n\n".join(lines)

    async def send(self, text: str, attachments: List[str] = None) -> str:
        """发送并等待回复（接口与 DeepSeekSession.send 一致）"""
        full_context = self._build_context(text)
        self._messages.append({"role": "user", "content": text})

        # 发送（含附件）
        try:
            await self._bm.send_message(full_context, attachments=attachments)
        except Exception as e:
            raise RuntimeError(f"[{self._bm.profile.name}] 发送失败: {e}")

        # 等待回复（第三方平台流式较慢，给 120s），实时抓取「思考过程」
        response = await self._bm.wait_response(
            timeout=120,
            on_thinking=self._emit_thinking,
            thinking_selector=self._bm.profile.thinking_selector,
        )
        # 确保最终思考内容已上报（避免末尾事件被缓冲截断）
        if self._last_thinking and self._on_event:
            try:
                self._on_event("ai_thinking", {"text": self._last_thinking})
            except Exception:
                pass
        if not response:
            response = "（未收到回复）"
        self._messages.append({"role": "assistant", "content": response})
        logger.info(f"[{self._bm.profile.name}] 对话完成，历史 {len(self._messages)} 条")

        # ===== 自动持久化（修复「历史记录是坏的」：第三方平台对话也要落盘）=====
        self._maybe_save_conversation()

        return response

    # ---- 对话历史持久化（与 DeepSeekSession 对齐）----
    def _maybe_save_conversation(self):
        """每 5 轮或会话结束时自动持久化到全局对话历史"""
        user_count = sum(1 for m in self._messages if m["role"] == "user")
        if user_count % 5 == 0 or user_count == 0:
            self._do_save_conversation()

    def _do_save_conversation(self):
        try:
            from .session import get_conversation_history, Message, _task_conv_dir
            hist = get_conversation_history()
            msgs = [Message(role=m["role"], content=m["content"]) for m in self._messages]
            hist.add_record(
                platform=self._bm.profile.platform,
                session_id="",
                url=self.get_current_url(),   # 修复：之前永远是空字符串
                messages=msgs,
                tags=[],
            )
            # 方案二备份：平台 JSON
            self._save_conv_json()
            logger.info(f"[{self._bm.profile.name}] 对话已自动持久化 (url={'有' if self.get_current_url() else '无'})")
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 历史持久化失败: {e}")

    def _save_conv_json(self, file_path: str = None) -> str:
        """方案二：把消息落盘成平台 JSON（含 URL，便于交叉校验）"""
        from .session import _task_conv_dir, _record_task
        if file_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = str(_task_conv_dir() / f"conv_{self._bm.profile.platform}_{ts}.json")
        data = {
            "platform": self._bm.profile.platform,
            "url": self.get_current_url(),
            "messages": [{"role": m["role"], "content": m["content"]} for m in self._messages],
        }
        Path(file_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 把这次对话登记为【一个独立任务】（标题=首条用户消息）
        _record_task(self._bm.profile.platform, file_path,
                     self.get_current_url(), self._messages)
        return file_path

    def get_current_url(self) -> str:
        if self._bm and hasattr(self._bm, "_page") and self._bm._page:
            return self._bm._page.url
        return ""

    def save_conversation(self, file_path: str = None) -> str:
        """手动保存对话（方案二：消息 JSON）+ 同步刷新方案一索引"""
        path = self._save_conv_json(file_path)
        try:
            self._do_save_conversation()
        except Exception as e:
            logger.warning(f"刷新 URL 索引失败（JSON 已存）: {e}")
        logger.info(f"[{self._bm.profile.name}] 对话已保存到 {path}")
        return path

    def load_conversation(self, file_path: str) -> bool:
        """从文件加载对话历史（方案二）"""
        try:
            data = json.loads(Path(file_path).read_text(encoding="utf-8"))
            self._messages.clear()
            for m in data.get("messages", []):
                self._messages.append({"role": m["role"], "content": m["content"]})
            logger.info(f"[{self._bm.profile.name}] 对话已从 {file_path} 加载，共 {len(self._messages)} 条")
            return True
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 加载对话失败：{e}")
            return False

    # ============================================================
    # 历史恢复：方案一(URL 追溯) 优先，失败回退方案二(消息 JSON)
    # ============================================================
    async def restore_conversation(self, task_id: str = None) -> bool:
        """恢复一个【独立任务】的历史（手动触发，绝不自动执行）。

        - task_id 给定：恢复该指定任务；- 为 None：恢复最新任务。
        """
        if task_id:
            from .session import _get_task
            t = _get_task(task_id)
            if t and t.get("file") and self.load_conversation(t["file"]):
                logger.info(f"[{self._bm.profile.name}] 历史恢复：指定任务 {task_id} 成功")
                return True
            logger.warning(f"未找到任务 {task_id}，回退到最新任务")
        if await self._restore_from_url():
            logger.info(f"[{self._bm.profile.name}] 历史恢复：方案一(URL 追溯) 成功")
            return True
        if self._restore_from_json():
            logger.info(f"[{self._bm.profile.name}] 历史恢复：回退方案二(消息 JSON) 成功")
            return True
        logger.info(f"[{self._bm.profile.name}] 历史恢复：无历史可恢复")
        return False

    def list_tasks(self) -> list:
        """列举本平台全部独立任务（按更新时间倒序）"""
        from .session import _list_tasks
        return _list_tasks(platform=self._bm.profile.platform)

    async def start_new_conversation(self):
        """开一个全新的独立对话：清空本地上下文 + 在浏览器里导航到新聊天。"""
        self._messages.clear()
        self._session_id = ""
        try:
            if self._bm and getattr(self._bm, "navigate_to_chat", None):
                await self._bm.navigate_to_chat()
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 新建对话（浏览器侧）失败: {e}")

    async def _restore_from_url(self) -> bool:
        from .session import get_conversation_history
        try:
            rec = get_conversation_history().get_latest(platform=self._bm.profile.platform)
        except Exception:
            return False
        if not rec or not rec.url:
            return False
        try:
            await self._bm.navigate(rec.url)
            if not await self._bm.check_login():
                logger.warning("URL 追溯：未登录，回退方案二")
                return False
            msgs = await self._read_existing_messages()
            if msgs:
                self._messages = msgs
                return True
        except Exception as e:
            logger.warning(f"URL 追溯失败，回退方案二: {e}")
        return False

    def _restore_from_json(self) -> bool:
        from .session import _latest_conv_file
        path = _latest_conv_file(self._bm.profile.platform)
        if path and self.load_conversation(str(path)):
            return True
        return False

    async def _read_existing_messages(self) -> list:
        """从浏览器 DOM 读回已有对话（方案一用）。

        复用 _read_last_reply 的逐行清洗逻辑，过滤网页自带 UI 文字（「快速」/
        「人工智能生成的内容可能不准确」等），避免把噪音读进历史。
        """
        if not (self._bm and getattr(self._bm, "_page", None)):
            return []
        sel = self._bm.profile.response_selector
        try:
            raw = await self._bm._page.evaluate(
                "(args) => {"
                " const sel = args[0]; const noise = args[1];"
                " const cleanPart = (raw) => {"
                "   const lines = (raw || '').split(/\\r?\\n/); const kept = [];"
                "   for (const line of lines) {"
                "     const s = line.trim(); if (!s) continue;"
                "     let isUi = false;"
                "     for (const n of noise) { if (s === n) { isUi = true; break; } }"
                "     if (isUi) continue;"
                "     let stripped = s;"
                "     for (const n of noise) { stripped = stripped.split(n).join(''); }"
                "     if (stripped.trim().length === 0) continue;"
                "     kept.push(line);"
                "   }"
                "   return kept.join('\\n').trim();"
                " };"
                " const els = document.querySelectorAll(sel);"
                " const out = [];"
                " for (const el of els) {"
                "   const tag = el.tagName;"
                "   if (tag === 'TEXTAREA' || tag === 'INPUT') continue;"
                "   if (el.getAttribute && el.getAttribute('contenteditable') !== null) continue;"
                "   const t = (el.innerText || '').trim();"
                "   if (!t) continue;"
                "   const c = cleanPart(t);"
                "   if (c) out.push(c);"
                " }"
                " return out;"
                " }",
                [sel, UI_NOISE_TOKENS],
            )
            msgs = []
            for i, t in enumerate(raw or []):
                role = "user" if i % 2 == 0 else "assistant"
                msgs.append({"role": role, "content": t})
            return msgs
        except Exception as e:
            logger.warning(f"从浏览器读回对话失败: {e}")
            return []

    def clear_history(self):
        self._messages.clear()
