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
import re
import shutil
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger("platform_browser")

# ── 同平台绝不双开的进程内互斥（核心防护）──
# 同一个 user_data_dir（同一平台 profile）绝不能被两个持久化上下文同时占用——
# 后开的会杀掉先开的活浏览器 / 抢 SingletonLock，导致「一个 deepseek 掉登」。
# 不同平台的目录（deepseek 与 豆包）互不相干，可各开各的、并存多浏览器。
# 这里用「目录 -> owner 管理器 + 锁」做进程内单例互斥：
#   - 同目录第二个 manager 来时，要么拿到 owner 正在持有的 context 接管复用，
#     要么在 owner 尚未 launch 时排队等它 launch 完再接管（绝不自己再开一个）。
# 这是「同平台最多 1 个浏览器进程、跨平台可并存」的落地机制。
_lock_by_dir: "Dict[str, Any]" = {}          # 目录str -> 保护该目录 launch 的 asyncio.Lock
_owner_by_dir: "Dict[str, Any]" = {}         # 目录str -> 已持有该目录 context 的 manager 实例
_dir_identity: "Dict[str, Path]" = {}        # manager id -> 其 user_data_dir（动态跟踪 fresh_profile 等）

_DIAG_FILE = r"D:\软件\XianRenZhangAgent\_xrz_dom_dump.jsonl"


def _write_diag(platform_name: str, tag: str, data) -> None:
    """把诊断数据直接写文件并 flush，绕过 stdout 块缓冲（重定向到文件时日志会卡在 8KB 缓冲里）。"""
    try:
        with open(_DIAG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"platform": platform_name, "tag": tag, "data": data},
                               ensure_ascii=False) + "\n")
            f.flush()
    except Exception:
        pass

# 网页自带的 UI 文字（开关/按钮标签/免责声明等），绝不能当作 AI 回复读进上下文。
# 这些文字一旦被当成 assistant 回复读进去，下一次又会作为上下文发给模型，造成污染。
# 典型来源（chat.qwen.ai）：
#   - 输入框旁的「快速回答」开关文字
#   - 回复底部的免责声明「人工智能生成的内容可能不准确」
#   - 回复操作按钮「复制 / 赞 / 踩 / 重新生成 / 分享」
def _has_complete_protocol(text: str) -> bool:
    """文本里是否已经含有一个【完整可解析】的工具调用块（@@@@ 或 RAW）。

    用途：给 _looks_incomplete 兜底——只要协议块已经完整闭合、能被解析器解析出来，
    就不该再因为「疑似写到一半」而空转等待。实测豆包回复
    `@@@@ {"tool":"set_deep_think",...} @@@@` 时曾被误判 10 次（白白多等 20 秒）。
    """
    try:
        from .protocol import Protocol
        return Protocol().extract(text or "") is not None
    except Exception:
        return False


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
    # qwen 思考模式下拉框的模式标签（Auto/自动）——曾被误读成 AI 回复
    "Auto", "自动",
    # 各平台「思考过程」折叠条上的状态标签（实测 chat.qwen.ai 会在正文前多出一行
    # 「已经完成思考」，被整行读进回复里污染上下文）
    "已经完成思考", "已完成思考", "思考完成", "已深度思考", "深度思考完成",
    "已思考", "思考已结束", "以上是思考过程",
    # 生成中的状态行（实测元宝在思考期只渲染「正在思考」，被整段读成 AI 回复）
    "正在思考", "思考中", "正在深度思考", "正在生成", "生成中",
    "正在搜索", "正在联网搜索", "正在分析", "正在输入",
    # Qwen Studio 的「选一个更好的回复」反馈面板（实测会被整段读进回复里）
    "此反馈将帮助我们评估并提升", "你更喜欢哪个回复", "请选择一个以继续",
    "我更喜欢这个回复", "更喜欢这个回复", "此反馈将帮助我们",
    # 元宝的 A/B 反馈面板（实测 2026-09-13：元宝在回复后弹出
    # 「您正在提供关于 元宝 新版本的反馈 / 您更喜欢哪个回答 / 回答1 回答2 /
    #   我更喜欢这个回答」，整段被当成模型回复读回来，把真正的回答顶掉了）
    "您正在提供关于", "您更喜欢哪个回答", "我更喜欢这个回答",
    "更喜欢这个回答", "回答 1", "回答 2", "此反馈仅用于改进",
    # 平台「额度 / 次数耗尽」提示卡（实测豆包会把这张卡整段渲染在回复区，
    # 被我们当成模型回答读回来，导致任务被误判成「模型不回协议」）
    "免费额度用完", "免费额度已用完", "额度用完", "额度已用完", "额度用完了",
    "免费额度不足", "额度不足", "次数已用完", "使用次数已达上限",
    "恢复为你服务", "开通豆包订阅", "免等待，继续为你服务", "休息一阵子",
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
    # ── 「联网搜索」开关的按钮选择器（如 DeepSeek 的「智能搜索」）──
    # 空列表 = 该平台页面没有联网搜索开关，set_search() 直接返回 False（不瞎点按钮）。
    search_toggle_selectors: list = field(default_factory=list)
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
    # 深度思考超时（秒）：仅对通义千问等可能长时间思考的平台有效。
    # 设为 None 或 0 表示不启用超时跳过。
    thinking_timeout: int = 0
    # 跳过思考按钮选择器（超时后点击此元素跳过思考）
    skip_thinking_selector: str = ""
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
    # 「用户自己发出去的消息气泡」选择器（结构性识别）。
    # 为什么需要：豆包等平台的用户气泡与 AI 气泡共用同一个 response_selector
    # （如 [class*="md-box-root"]），若只按文本比对，网页重排/裁剪导致字符数差几十个
    # 就会漏判 → 把自己发出去的提示词当成 AI 回复读回来（表现为「8 秒就"回复完成"，
    # 内容却是自己刚发的那段」）。配上本选择器后，直接按 DOM 结构排除用户气泡。
    user_bubble_selector: str = ""
    # 该平台（当前网页界面）是否真的提供「深度思考」开关。
    # 实测豆包网页端并没有可用的深度思考 toggle：原实现退而去点工具栏的
    # 「快速 / 深入研究」模式按钮，结果把页面点进了异常状态，之后 210s 都收不到任何回复。
    # 因此这里允许直接标注为 False —— 此时 enable_thinking 立即返回 False（不点任何按钮），
    # 由上层如实告诉模型「该平台没有深度思考开关」，而不是谎报成功、更不是把页面点坏。
    supports_thinking: bool = True

PLATFORM_PROFILES = {
    "deepseek": PlatformProfile(
        platform="deepseek",
        name="DeepSeek",
        url="https://chat.deepseek.com",
        chat_url="https://chat.deepseek.com",
        input_selector="textarea",
        # 2026-09-20 真机 DOM 核对：新版 DeepSeek 的发送键已不是 button[type='submit']（0 匹配），
        # 置空走 CDP 受信 Enter（windowsVirtualKeyCode=13，实测可用）。详见 platforms.json _send_note。
        send_selector="",  # 用 Enter 发送（新版页面已无 submit 按钮）
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
        send_selector="",  # 用 Enter 发送
        upload_button_selector="",  # 保留字段；实际用 attach_button_selector
        # 深度思考开关：千问是 ant-select 下拉框，需选「思考模式」
        thinking_mode="select",
        thinking_toggle_selectors=[
            "div[class*='qwen-select-thinking'] .ant-select-selector",
            "div.qwen-select-thinking .ant-select-selector",
            "div.qwen-select-thinking",
            "div.qwen-thinking-selector",
        ],
        # 文件上传：千问点「+」后是下拉菜单，需选「上传附件」
        attach_button_selector="[aria-label='选择模式']",
        attach_menu_item_text="上传附件",
        multi_file=True,
        response_selector=".qwen-chat-message-assistant",
        thinking_selector="[class*='thinking'], [class*='reasoning'], [class*='think'], [data-testid*='thinking'], details:has(summary)",
        generating_selector=".stop-button",
        thinking_timeout=60,
        skip_thinking_selector="button:has-text('跳过'), button:has-text('取消'), span:has-text('跳过思考')",
        login_texts=["登录", "Login"],
        viewport_width=1280,
        viewport_height=900,
        display="通义千问",
        icon="💬",
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
    # 先加载 JSON 原始数据以获取 models 等额外字段
    raw: dict = {}
    for path, _tag in [
        (Path(__file__).parent / "platforms.json", "内置"),
        (DATA_ROOT / "platforms.user.json", "用户"),
    ]:
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    raw.update(d)
            except Exception:
                pass
    out = []
    for key, p in PLATFORM_PROFILES.items():
        name = getattr(p, "name", key)
        display = getattr(p, "display", "") or name
        icon = getattr(p, "icon", "") or "🌐"
        url = getattr(p, "url", "")
        chat_url = getattr(p, "chat_url", url)
        tm = getattr(p, "thinking_mode", "toggle")
        tto = getattr(p, "thinking_timeout", 0) or 0
        sts = getattr(p, "skip_thinking_selector", "") or ""
        # 从 JSON 中补充 models（dataclass 会忽略未知字段）
        models = []
        if key in raw and isinstance(raw[key].get("models"), list):
            models = raw[key]["models"]
        out.append({
            "key": key,
            "name": name,
            "display": display,
            "icon": icon,
            "url": url,
            "chat_url": chat_url,
            "thinking_mode": tm,
            "thinking_timeout": tto,
            "skip_thinking_selector": sts,
            "attach_button_selector": getattr(p, "attach_button_selector", "") or "",
            "multi_file": getattr(p, "multi_file", True),
            "models": models,
            # 该平台网页是否真的带「联网搜索」开关（供 GUI 决定是否显示）
            "has_search": bool(getattr(p, "search_toggle_selectors", None)),
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
        # 【子代理支持】子窗口标志：子窗口与母代理共享同一 _browser（context），
        # 只拥有自己的一个新 page。close() 时只关自己的 page，绝不动母代理的
        # 浏览器进程 / playwright（与 browser.py 的 BrowserManager._is_child 同款设计）。
        self._is_child = False
        # 发送前读取的消息数（由 send_message 写入，供 wait_response 作基线）
        # 关键修复：千问/通义在提交瞬间就创建新消息 DOM 节点，若 baseline 在
        # 发送后读，count 永远 == baseline → appeared 恒为 False → 等待回复超时。
        # 改为「发送前先存计数」，确保出现了新节点才会 detected。
        # 【关键修复】用 None 作「未设置」哨兵，而不是 0——空对话时发送前计数
        # 合法地就是 0，若用 0 当哨兵，wait_response 会误判为「未设置」而回退到
        # 「发送后读计数」，此时新回复已被算进 baseline → appeared 恒为 False →
        # 第一轮必然超时返回「（未收到回复）」。这正是第三方平台（新会话）首轮
        # 对话失败的根因（DeepSeek 因历史记录多而侥幸不触发）。
        self._pre_send_msg_count = None
        # 【同平台绝不双开】是否「接管了同平台 owner 的 context」：
        # True = 与 owner 共享同一浏览器进程，close() 只清自己的 page，绝不关 owner。
        self._adopted = False
        # 僵尸检测用：浏览器主进程 PID 集 + context 关闭标记
        self._chrome_pids = None
        self._ctx_closed = False

    def _find_browser_pids(self):
        """找到本平台 context 对应 Chromium 主进程 PID（僵尸检测用）。"""
        import psutil
        try:
            marker = Path(self.user_data_dir).name.lower()
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
        """同步判定 self._browser 是否僵尸（进程死但 Playwright 侧还挂着）。"""
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
        """僵尸则强制清理，让 launch() 走完整重建。"""
        if self._browser is None:
            return
        dead, reason = self._is_zombie()
        if not dead:
            return
        logger.warning(f"[{self.profile.name}] 检测到僵尸浏览器（{reason}），强制清理后重建...")
        self._close_context_and_cleanup()

    def _close_context_and_cleanup(self):
        """把死 context 的所有引用清零（不清 registry——只有 close() 才释放 owner 登记）。"""
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
            logger.warning(f"[{self.profile.name}] 浏览器上下文已关闭（close 监听），下次调用将自动重建")
        except Exception:
            pass

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

        # 【僵尸自愈】浏览器进程被杀/崩溃后 context 对象可能还挂着（is_closed() 不靠谱），
        # 旧逻辑「_browser is not None 就复用」→ 永远复用死 context → 页面未初始化。
        # 现在做 PID 级存活验证，僵尸一律清理后走完整重建。
        if self._browser is not None and not fresh_profile:
            await self._clear_zombie()
            if self._browser is not None:
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

        # 【同平台防双开】同目录（同一平台）已有活 owner → 直接接管复用，
        # 绝不重开、绝不 kill owner（kill 活浏览器 = 掉登）。
        # 不同平台目录不同（键不同）互不影响 → 一个 DeepSeek + 一个豆包可以并存。
        from agent_core import profile_lock as _pl
        if not fresh_profile:
            _owner = _pl.acquire(self.user_data_dir, self)
            if _owner is not None and _owner is not self:
                logger.info(f"[{self.profile.name}] 同目录已有活 owner（{type(_owner).__name__}），"
                            f"接管复用（同平台不双开，防掉登）")
                # context 绑定 owner 的 Playwright 连接，必须共享 owner 的实例
                self._playwright = _owner._playwright or self._playwright
                self._browser = _owner._browser
                self._chrome_pids = getattr(_owner, "_chrome_pids", None)
                self._adopted = True
                self._ctx_closed = False
                try:
                    self._browser.on("close", self._on_context_closed)
                except Exception:
                    pass
                self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
                await self._load_cookies()
                logger.info(f"[{self.profile.name}] 已接管同目录 owner 的浏览器（零双开）")
                return

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        # 2.1 清残留进程 + 锁文件。
        # 【关键】_kill_stale_browser_for_profile() 会杀「所有命令行含本目录」的活进程——
        # 包括另一个还活着的浏览器。现在走到这里说明本目录在进程内【没有活 owner】，
        # 杀到的只可能是跨进程孤儿残留（上次崩溃留下的），是安全的。
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
            self._browser.on("close", self._on_context_closed)
        except Exception:
            pass
        # 记录浏览器主进程 PID + 关闭标记，供僵尸检测（launch/navigate 自愈用）
        self._chrome_pids = self._find_browser_pids()
        self._ctx_closed = False

        # 4. 初始化页面
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()

        # 4.5 Windows：给浏览器窗口换上仙人球图标（标题栏 + 任务栏）。
        #     Playwright Chromium 的窗口图标默认是 Chromium logo，用户明确要求
        #     换成仙人球。窗口可能晚于 launch 出现，故后台重试若干次。
        try:
            asyncio.get_event_loop().create_task(self._apply_cactus_icon_loop())
        except Exception:
            pass

        # 5. 加载 cookies
        await self._load_cookies()

        logger.info(f"[{self.profile.name}] 浏览器启动完成")

    def _win_apply_cactus_icon(self) -> int:
        """实例方法包装：委托给模块级 _win_apply_cactus_icon()"""
        return _win_apply_cactus_icon()

    async def _apply_cactus_icon_loop(self):
        """后台轮询给浏览器窗口设置仙人球图标。

        【2026-09-21 修复】原来「设置成功 >0 就 return」，导致图标只贴到 launch
        后最先出现的**临时窗口**上，晚几秒才出现的真正主窗口永远没人管。
        现在改成**持续**巡检一段时间，不做成功即停；此外模块级还有常驻守护线程兜底。
        """
        try:
            total = 0
            for _ in range(30):          # ~60s 内反复补，覆盖窗口陆续出现的窗口期
                total += _win_apply_cactus_icon()
                await asyncio.sleep(2.0)
            if total:
                logger.info(f"[{self.profile.name}] 浏览器窗口图标已设为仙人球 🌵（共补 {total} 次）")
        except Exception:
            pass


# ── 模块级图标设置函数（被 terminal.py 的全局常驻任务调用） ──
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

    async def _persist_cookies_throttled(self, force: bool = False):
        """已登录确认后，把当前全部 cookie（含【非持久会话 cookie】）落盘到 cookies.json，
        使登录态能跨 Chromium / 后端重启存活。

        【09-14 根因】元宝（yuanbao.tencent.com）靠 2 条【非持久】会话 cookie 维持登录，
        Chromium 每次被杀（重启后端）就把它们从内存清掉；虽然 10 条 tencent.com SSO
        持久 cookie 还在，但元宝 app 不会自动靠 SSO 补发会话 cookie → 掉回扫码登录墙。
        而 save_cookies() 原先只在 wait_login() 成功时调一次，正常运行从不落盘，
        会话 cookie 一重启即丢（实测今早后端被杀 3 次后元宝就掉登录）。
        这里在 check_login 判「已登录」时就落盘，重启后 _load_cookies() 把会话 cookie
        加回，登录态不再因重启而丢。带 60s 节流，避免高频 check_login 反复写盘
        （force=True 可绕过，如刚扫码成功）。未登录时绝不调用它（只调用已登录路径）。"""
        if not self._browser:
            return
        import time as _time
        now = _time.time()
        if not force and (now - getattr(self, "_last_cookie_save_ts", 0.0)) < 60:
            return
        try:
            await self.save_cookies()
            self._last_cookie_save_ts = now
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 持久化 cookies 失败: {e}")

    async def navigate(self, url: str = None):
        """别名：与 BrowserManager 接口对齐，便于外部按 self.navigate() 调用。
        url 参数：若提供则导航到指定 URL；否则导航到本 profile 的 chat_url。"""
        if url:
            if not self._page:
                raise RuntimeError("页面未初始化")
            await self._page.goto(url, wait_until="commit", timeout=60000)
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
        # 【僵尸自愈】进程被杀后 context 对象可能还挂着，先判死，避免无谓进入重试循环
        await self._clear_zombie()
        if self._browser is None and self._page is None:
            await self.launch()
            if self._page is None:
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
                                     wait_until="commit", timeout=60000)
                # 设置浏览器窗口标题为平台名称（便于识别）
                try:
                    await self._page.evaluate(f"document.title = '{self.profile.name} - 仙人掌 Agent'")
                except Exception:
                    pass
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
                diag = await self._page.evaluate("""() => {
                    const reasons = [];
                    const vis = (el) => !!(el && el.offsetWidth > 0 && el.offsetHeight > 0);

                    // 1. 底部/左上角「未登录」头像/文字（必须可见）
                    const bottomLogin = document.querySelector(
                        '[class*="bottom"], [class*="footer"] [class*="login"], ' +
                        '[class*="user"] [class*="login"], [class*="avatar"]');
                    if (vis(bottomLogin) && /未登录/.test((bottomLogin.innerText || '').trim()))
                        reasons.push('1.底部未登录');

                    // 2. 页面主区域【可见】的 nologin class
                    for (const c of document.querySelectorAll('[class*="nologin"]')) {
                        if (vis(c)) { reasons.push('2.可见nologin'); break; }
                    }

                    // 3. 右上角【可见】顶部导航栏「登录」按钮
                    for (const b of document.querySelectorAll('header [class*="login"], [class*="header"] [class*="login"], .yb-nav__user [class*="login"]')) {
                        if (vis(b) && b.innerText.trim()) { reasons.push('3.右上角登录按钮'); break; }
                    }

                    // 4. 整页扫码登录卡片专属文案（掉登录整页就是这张卡片）
                    const bodyText = document.body.innerText || '';
                    const LOGIN_WALL = ['请使用微信扫描二维码登录', '请扫描二维码登录',
                                        '扫码默认已阅读', '扫码登录'];
                    for (const k of LOGIN_WALL) {
                        if (bodyText.includes(k)) { reasons.push('4.扫码卡片:' + k); break; }
                    }

                    // 5. 【收紧 09-14】仅当 nologin 元素【可见】且确无会话内容时才算未登录。
                    // 旧写法 !hasMessage && 存在任意 nologin（含隐藏）——元宝 SPA 在已登录页也会
                    // 预渲染隐藏的 login 相关组件，导致 check_login 误报未登录（实测 09-14：
                    // tencent.com 10 条有效登录 cookie 在线，却被判未登录）。
                    const hasMessage = document.querySelectorAll('[class*="message"], [class*="session"], [class*="dialog"]').length > 0;
                    const nologinVisible = Array.from(document.querySelectorAll('[class*="nologin"]')).some(vis);
                    if (!hasMessage && nologinVisible) reasons.push('5.可见nologin+无会话');

                    // 6. 未登录时输入框 placeholder（保留原兜底，恒不判未登录）
                    const editor = document.querySelector('[contenteditable].ql-editor, textarea');
                    if (editor && /登录|请输入/.test(editor.getAttribute('placeholder') || '')) {
                        // 先不据此判定
                    }

                    return { nologin: reasons.length > 0, reasons: reasons };
                }""")
                _reasons = (diag.get("reasons") or []) if isinstance(diag, dict) else []
                if isinstance(diag, dict) and diag.get("nologin"):
                    logger.info(f"[{self.profile.name}] check_login: 未登录（命中: {','.join(_reasons) or '无'}）")
                    return False

                # 页面还在骨架屏 / 加载中 → 登录墙很可能还没渲染出来，
                # 此时 is_nologin=false 是【假阴性】。
                # 实测：元宝刚导航完 0.5s 就报告「已登录」，实际是未登录的登录墙，
                # 于是照常发送 → 点击输入框超时 15s → 空等回复 40s → 才发现没登录，
                # 用户白等近 90 秒。这里等到页面稳定后再复检一次。
                if not getattr(self, "_login_rechecking", False):
                    self._login_rechecking = True
                    try:
                        _skeleton_js = """() => {
                            if (document.readyState !== 'complete') return true;
                            return document.querySelectorAll(
                                '[class*="skeleton" i], [class*="loading" i]').length > 3;
                        }"""
                        try:
                            _loading = await self._page.evaluate(_skeleton_js)
                        except Exception:
                            _loading = False
                        if _loading:
                            logger.info(f"[{self.profile.name}] 页面仍在加载（骨架屏），"
                                        f"等待稳定后复检登录状态…")
                            for _ in range(10):
                                await asyncio.sleep(1.0)
                                try:
                                    if not await self._page.evaluate(_skeleton_js):
                                        break
                                except Exception:
                                    break
                            return await self.check_login()
                    finally:
                        self._login_rechecking = False

                logger.info(f"[{self.profile.name}] check_login: 已登录")
                await self._persist_cookies_throttled()
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
            except Exception:
                pass

            # 页面还在骨架屏 / 加载中时【不能急着下结论】：
            # 此刻登录入口还没渲染（has_login=False），消息也还是 0（has_content=False），
            # 按 `not has_login` 的规则会被判成「已登录」——典型的假阳性。
            # 实测：元宝刚导航完 0.5s 就报告「已登录」，实际是未登录的登录墙，
            # 于是照常发送 → 点击输入框超时 15s → 空等回复 40s → 才发现没登录，
            # 用户白白干等近 90 秒才看到一句「请先登录」。
            # 这里先确认页面是否仍在加载，是就等它稳定再判断（最多 ~10s）。
            if not has_login and not has_content:
                try:
                    _loading = await self._page.evaluate("""() => {
                        if (document.readyState !== 'complete') return true;
                        const sk = document.querySelectorAll(
                            '[class*="skeleton" i], [class*="loading" i]').length;
                        return sk > 3;
                    }""")
                except Exception:
                    _loading = False
                if _loading:
                    logger.info(f"[{self.profile.name}] 页面仍在加载（骨架屏），"
                                f"等待稳定后再判定登录状态…")
                    for _ in range(10):
                        await asyncio.sleep(1.0)
                        try:
                            has_login = await login_btn.count() > 0
                            has_content = await self._page.locator(
                                "[class*='message']").count() > 0
                        except Exception:
                            break
                        if has_login or has_content:
                            break

            logged_in = not has_login or has_content
            logger.info(f"[{self.profile.name}] 登录状态: {logged_in}")
            if logged_in:
                await self._persist_cookies_throttled()
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
            await self._page.wait_for_selector(self.profile.input_selector, timeout=30000)
        except:
            logger.warning(f"[{self.profile.name}] 输入框选择器失败，尝试通用选择器")
            await self._page.wait_for_selector("textarea, [contenteditable='true']", timeout=30000)

        # 查找输入框
        input_el = None
        try:
            input_el = self._page.locator(self.profile.input_selector).first
            if await input_el.count() == 0:
                input_el = self._page.locator("textarea").first
        except:
            input_el = self._page.locator("textarea").first

        if not await input_el.is_visible():
            logger.warning(f"[{self.profile.name}] 输入框初始不可见，等待可见后再填")

        # 清空并填写
        # 关键修复：千问切平台/React 路由切换时，输入框可能短暂不可交互（loading/隐藏层遮挡）。
        # 先用 wait_for("attached") 保证 DOM 节点已插入，再 click 聚焦；fill 加显式超时 45s
        # （默认 30s 在 React 页面重绘时可能不够），避免「发送失败: Locator.fill: Timeout」假失败。
        # 子代理新开页面时输入框可能更慢：先等可见（不强制 raise），click 超时放宽到 15s，
        # click 失败则回退到 focus + fill，避免「发送失败: Locator.click: Timeout」假失败。
        try:
            await input_el.wait_for(state="visible", timeout=15000)
        except Exception as e:
            logger.warning(f"[{self.profile.name}] 输入框15s内未可见，仍尝试填写: {e}")
        await input_el.wait_for(state="attached", timeout=15000)
        try:
            await input_el.click(timeout=15000)
        except Exception as e:
            logger.warning(f"[{self.profile.name}] click 失败，改 focus+fill: {e}")
            try:
                await input_el.focus(timeout=5000)
            except Exception:
                pass
        await asyncio.sleep(0.2)
        await input_el.fill("", timeout=45000)
        await asyncio.sleep(0.1)
        # 用 Playwright 标准 fill（触发完整输入事件链含 React onChange），千问 React 只认这个
        _tag = ""
        try:
            _tag = (await input_el.evaluate("el => el.tagName") or "").upper()
        except Exception:
            pass
        if _tag == "TEXTAREA":
            await input_el.fill(text, timeout=45000)
        else:
            try:
                await self._page.evaluate(
                    """(args) => { const el = args.el, txt = args.txt;
                        el.innerText = txt;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true })); }""",
                    {"el": await input_el.evaluate_handle("el => el"), "txt": text},
                )
            except Exception:
                await input_el.fill(text, timeout=45000)
        await asyncio.sleep(0.3)

        # 发送前：记录当前消息数（用于验证发送成功，也作为 wait_response 的基线）
        # 关键修复：千问/通义在「提交瞬间」就创建新消息节点，若 wait_response 在
        # 发送后才读基线，会把新节点算进 baseline → count 永远不 > baseline →
        # appeared 恒为 False → 捕获 0 字 → 等待回复超时假失败。故发送前就存好基线。
        msg_count_before = 0
        try:
            msg_count_before = await self._page.evaluate(
                "(sel) => document.querySelectorAll(sel).length",
                self.profile.response_selector,
            ) or 0
        except Exception:
            pass
        self._pre_send_msg_count = msg_count_before
        # 记住本轮发出去的原话：豆包/千问等平台「用户气泡」和「AI 气泡」共用同一个
        # class（例如豆包的 [class*="md-box-root"]），空会话 baseline=0 时会把用户自己
        # 刚发的那句话也当成 AI 回复读回来 → 回灌给模型后会污染上下文、甚至让模型
        # 以为「我已经回答过了」。这里存起来，读取回复时按内容剔除。
        self._last_sent_text = text

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
            # 使用 Enter 键发送。关键修复：千问等平台的 React onKeyDown 依赖事件
            # keyCode，Playwright press("Enter") 的合成事件 keyCode 不被识别，
            # 会被当成换行（消息永远发不出去 → 「未收到回复」假失败，实测确认）。
            # 必须用 CDP 派发带 windowsVirtualKeyCode=13 的浏览器级受信按键。
            _enter_ok = False
            try:
                cdp = await self._page.context.new_cdp_session(self._page)
                for evt in ("keyDown", "keyUp"):
                    await cdp.send("Input.dispatchKeyEvent", {
                        "type": evt, "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                    })
                _enter_ok = True
                logger.info(f"[{self.profile.name}] 已通过 CDP 受信 Enter 键发送")
            except Exception as e:
                logger.warning(f"[{self.profile.name}] CDP Enter 失败({type(e).__name__})，回退普通按键")
            if not _enter_ok:
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
                # 方式2：检查输入框是否已清空。
                # 注意：contenteditable（豆包/元宝的富文本编辑器）没有 input_value()，
                # 旧代码直接调 input_value() 会抛异常 → 被 except 吞掉 → verified 恒为 False，
                # 于是每次发送都打「可能未成功发出」的假警告。这里改用通用 DOM 取值。
                val = await self._page.evaluate(
                    """() => {
                        const inp = document.querySelector('div.tiptap.ProseMirror, [contenteditable], textarea');
                        if (!inp) return '';
                        return (inp.innerText || inp.value || '');
                    }"""
                )
                if not str(val or "").strip():
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
            # ---- 诊断：dump 输入框状态 + 可见的发送类按钮，便于修正 send 方式 ----
            try:
                _sd = await self._page.evaluate(
                    """() => {
                        const res = {url: location.href, inputText: '', msgClasses: {}, tail: []};
                        const inp = document.querySelector('div.tiptap.ProseMirror, [contenteditable], textarea');
                        if (inp) res.inputText = (inp.innerText || inp.value || '').slice(0, 60);
                        const cnt = {};
                        document.querySelectorAll('*[class]').forEach(el => {
                            const c = el.className;
                            if (typeof c !== 'string' || !c) return;
                            c.split(/\\s+/).forEach(t => {
                                if (/message|chat|bubble|reply|answer|markdown|content|assistant|user|segment|item|md-|receive|send/i.test(t)) {
                                    cnt[t] = (cnt[t] || 0) + 1;
                                }
                            });
                        });
                        res.msgClasses = cnt;
                        const main = document.querySelector('main') || document.body;
                        res.tail = Array.from(main.querySelectorAll('div[class]')).slice(-45)
                                     .map(el => (typeof el.className === 'string' ? el.className : '')).filter(Boolean);
                        return res;
                    }"""
                )
                _write_diag(self.profile.name, "send", _sd)
                logger.warning(f"[{self.profile.name}] 发送诊断: {str(_sd)[:2200]}")
            except Exception as _e:
                logger.warning(f"[{self.profile.name}] 发送诊断失败: {_e}")
        return True
    
    # ============================================================
    # 通用 toggle 开关（深度思考 / 智能搜索等，每个平台 UI 不同）
    # ============================================================
    async def _toggle_by_selectors(self, selectors, enable: bool, label: str) -> bool:
        """按选择器列表把一个 toggle 按钮切到目标状态。

        纯 UI 操作：检测激活态 → 不符就点 → 复检。全部候选失败返回 False。
        深度思考与智能搜索共用这套逻辑（两者在 DeepSeek 上都是
        div.ds-toggle-button，只是文案不同）。
        """
        if not self._page or not selectors:
            return False
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
                    if is_active == enable:
                        logger.info(f"[{self.profile.name}] {label}已是{'开启' if enable else '关闭'}")
                        return True
                    try:
                        await btn.click(timeout=3000)
                    except Exception as ce:
                        logger.warning(f"[{self.profile.name}] 点击 {sel} 失败: {ce}")
                        continue
                    await asyncio.sleep(0.8)
                    is_active2 = await self._detect_toggle_active(btn)
                    if is_active2 == enable:
                        logger.info(f"[{self.profile.name}] {label}已{'开启' if enable else '关闭'} (selector={sel})")
                        return True
                    logger.warning(
                        f"[{self.profile.name}] 点击 {sel} 后 {label}状态未变 "
                        f"({is_active}→{is_active2})，尝试下一个候选"
                    )
            except Exception as e:
                logger.warning(f"[{self.profile.name}] 尝试选择器 {sel} 失败: {e}")
                continue
        return False

    async def set_search(self, enable: bool) -> bool:
        """开启/关闭当前平台的「联网搜索」开关（如 DeepSeek 的「智能搜索」）。

        没有配置 search_toggle_selectors 的平台直接返回 False（不瞎点）。
        """
        selectors = getattr(self.profile, "search_toggle_selectors", None) or []
        if not selectors:
            logger.info(f"[{self.profile.name}] 该平台未提供联网搜索开关，跳过（不点击任何按钮）")
            return False
        ok = await self._toggle_by_selectors(selectors, enable, "智能搜索")
        if not ok:
            logger.warning(f"[{self.profile.name}] 未找到可用的联网搜索开关")
        return ok

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
        # 平台已明确标注「没有深度思考开关」→ 绝不点任何按钮（乱点会把页面点坏），
        # 直接返回 False，由上层如实告知模型。
        if not getattr(self.profile, "supports_thinking", True):
            logger.info(f"[{self.profile.name}] 该平台未提供深度思考开关，跳过（不点击任何按钮）")
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
        """读取当前页面最长的「思考过程」文本。

        【2026-09-21 真机加固】两条防线，保证拿到的是「推理正文」而不是折叠条标题：
          1) 优先读元素内部的 .ds-markdown（DeepSeek 实测推理正文就在这层，
             折叠标题 ._9ecc93a 在折叠态下 innerText 为空、展开态下只有
             「已思考（用时 N 秒）」这类一行状态，绝不该当成推理正文）；
             取不到再退回元素自身 innerText。
          2) 最后统一剥掉行首的「(已完成|已深度|已|完成)?(深度)?思考（用时 …）」
             这一行 —— 展开态下它会混进 innerText，剥掉才算干净。
        """
        if not selector or not self._page:
            return ""
        try:
            raw = await self._page.evaluate(
                "(sel) => {"
                " const els = document.querySelectorAll(sel);"
                " let best = '';"
                " for (const el of els) {"
                # 优先取推理正文层（.ds-markdown / .markdown / .ds-markdown-paragraph 的父层）
                "   let src = el.querySelector"
                "       ? (el.querySelector('.ds-markdown') || el)"
                "       : el;"
                "   let t = (src.innerText || '').trim();"
                "   if (!t) t = (el.innerText || '').trim();"
                "   if (t && t.length > best.length) best = t;"
                " }"
                " return best;"
                " }",
                selector,
            )
        except Exception:
            return ""
        if not raw:
            return ""
        # 第二道防线：剥掉折叠条标题行（整行或行首的「已思考（用时 N 秒）」等）
        try:
            import re as _re
            raw = _re.sub(
                r"^[\s]*(?:已完成|已深度|已|完成)?(?:深度)?思考[（(][^）)]{0,20}[）)][\s]*$",
                "", raw, flags=_re.M)
            raw = _re.sub(
                r"[\s]*(?:已完成|已深度|已|完成)?(?:深度)?思考[（(]用时[^）)]{0,20}[）)]",
                "", raw)
        except Exception:
            pass
        return raw.strip()

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
        # 第 4 个参数 = 本轮用户自己发出去的原话（用于剔除「用户气泡」被误读成 AI 回复）
        sent_text = getattr(self, "_last_sent_text", "") or ""
        # 第 5 个参数 = 平台「用户气泡」的选择器（结构性识别，比文本比对更可靠）
        user_bubble_sel = getattr(self.profile, "user_bubble_selector", "") or ""
        return await self._page.evaluate(
            "(args) => {"
            " const sel = args[0]; const base = args[1]; const noise = args[2];"
            " const norm = (s) => (s || '').replace(/\\s+/g, '').trim();"
            " const ex = norm(args[3] || '');"
            " const ubsel = args[4] || '';"
            # 【2026-09-21 真机实测】DeepSeek 每次回答在 DOM 里是两块：
            #   .ds-think-content                       ← 思考面板（「已思考（用时 N 秒）」+推理）
            #   .ds-assistant-message-main-content      ← 用户真正要看的回复
            # 旧实现整段读 innerText，把「已思考（用时 1 秒）」一起当成回复写进
            # 会话记录、还回灌给模型。这里结构性剔除：命中思考面板的元素直接跳过，
            # 并且不再把它内部文本拼进回复。
            " const inThinkPanel = (el) => {"
            "   if (!el) return false;"
            "   if (el.classList) {"
            "     const cn = el.className || '';"
            "     if (typeof cn === 'string' && cn.indexOf('ds-think-content') !== -1) return true;"
            "   }"
            "   let n = el;"
            "   for (let k = 0; k < 6 && n; k++) {"
            "     try {"
            "       if (n.classList && n.classList.contains"
            "           && n.classList.contains('ds-think-content')) return true;"
            "     } catch (e) {}"
            "     n = n.parentElement;"
            "   }"
            "   return false;"
            " };"
            " const isEcho = (t) => {"
            "   const a = norm(t);"
            "   if (!ex || a.length < 8) return false;"
            "   if (a === ex) return true;"
            "   const shorter = a.length < ex.length ? a : ex;"
            "   const longer = a.length < ex.length ? ex : a;"
            "   if (shorter.length >= 20 && longer.indexOf(shorter) !== -1"
            "       && longer.length - shorter.length <= Math.max(80, longer.length * 0.06)) return true;"
            "   const h = 160;"
            "   if (a.length >= 50 && ex.length >= 50 && a.slice(0, h) === ex.slice(0, h)) return true;"
            "   return false;"
            " };"
            " const inUserBubble = (el) => {"
            "   if (!ubsel) return false;"
            "   let n = el;"
            "   for (let k = 0; k < 8 && n; k++) {"
            "     try { if (n.matches && n.matches(ubsel)) return true; } catch (e) {}"
            "     try { if (n.querySelector && n.querySelector(ubsel)) return true; } catch (e) {}"
            "     n = n.parentElement;"
            "   }"
            "   return false;"
            " };"
            " const cleanPart = (raw) => {"
            "   const lines = (raw || '').split(/\\r?\\n/);"
            "   const kept = [];"
            "   for (const line of lines) {"
            "     const s = line.trim();"
            "     if (!s) continue;"
            "     let isUi = false;"
            "     for (const n of noise) {"
            "       if (s === n) { isUi = true; break; }"
            "       if (n && s.startsWith(n) && s.length < n.length + 40) { isUi = true; break; }"
            "     }"
            "     if (isUi) continue;"
            "     let stripped = s;"
            # 【2026-09-21 真机修复】DeepSeek 的思考面板折叠条文案是
            # 「已思考（用时 1 秒）」，它不是一个独立行，而是和后面的思考正文粘在
            # 同一行（innerText 拼出来是「已思考（用时 1 秒）\n\n用户要求…」，但有时
            # 会整段并进一行）。行级 startsWith 判据在这种「同一行内」的情况下会
            # 漏掉，于是这串 UI 文案留在回复里、还被写进会话 json 与后续上下文。
            # 这里做一次全文正则剔除：已思考/深度思考 +（用时 …）这个模式。
            "     stripped = stripped.replace("
            "        /(?:已完成|已深度|已|完成)?(?:深度)?思考（用时[^）]{0,20}）/g, '');"
            "     stripped = stripped.replace("
            "        /(?:已完成|已深度|已|完成)?(?:深度)?思考\\(用时[^)]{0,20}\\)/g, '');"
            "     for (const n of noise) { stripped = stripTok(stripped, n); }"
            "     stripped = stripped.trim();"
            "     if (!stripped) continue;"
            "     kept.push(stripped);"
            "   }"
            "   return kept.join('\\n').trim();"
            " };"
            " const HAN = /[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]/;"
            " const stripTok = (s, n) => {"
            "   if (!n) return s;"
            "   let out = ''; let i = 0;"
            "   while (true) {"
            "     const k = s.indexOf(n, i);"
            "     if (k < 0) { out += s.slice(i); break; }"
            "     const before = k > 0 ? s[k - 1] : '';"
            "     const after = (k + n.length) < s.length ? s[k + n.length] : '';"
            "     const embedded = (before && HAN.test(before)) || (after && HAN.test(after));"
            "     out += s.slice(i, embedded ? (k + n.length) : k);"
            "     i = k + n.length;"
            "   }"
            "   return out;"
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
            "     if (inThinkPanel(el)) continue;"
            "     if (inUserBubble(el)) continue;"
            "     if (isEcho(t)) continue;"
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
            "     if (inThinkPanel(el)) continue;"
            "     if (inUserBubble(el)) continue;"
            "     if (isEcho(t)) continue;"
            "     const cleaned = cleanPart(t);"
            "     if (!cleaned) continue;"
            "     text = cleaned; break;"
            "   }"
            " }"
            " return { count: count, text: text, newAppeared: count > base };"
            " }",
            [sel, baseline_count, UI_NOISE_TOKENS, sent_text, user_bubble_sel],
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
        # 「疑似未完整」连续次数：防止 _looks_incomplete 误判导致无限等待
        # （第三方平台回复里若夹带 @@@@ 示例/未配对括号，会被判为未完成而永不返回，
        #   表现为整轮卡满 120s 超时 → 母代理反复重试 → 用户侧看不到任何回复）
        incomplete_streak = 0
        gen_sel = self.profile.generating_selector or ""
        thinking_sel = thinking_selector or self.profile.thinking_selector
        text = ""
        appeared = False
        total_count = 0  # 诊断：页面上当前消息总数
        _no_appear_ticks = 0  # 连续「一个新气泡都没出现」的 tick 数（会话静默失效探测）
        _diag_tick = 0  # 诊断：每 20 次 tick 打印一次状态
        _mid_dumped = False  # 诊断：中期 DOM dump 只做一次
        # 思考开始时间（用于超时检测）
        self._thinking_start_time = None

        # 基线：优先用 send_message 写入的「发送前计数」（精准），否则回退到发送后读取。
        # 关键修复：千问/通义在「提交瞬间」就创建新消息节点，若在发送后再读 baseline，
        # 新节点会被算进 baseline → count 永远不 > baseline → appeared 恒为 False → 超时假失败。
        baseline = 0
        if getattr(self, '_pre_send_msg_count', None) is not None:
            baseline = self._pre_send_msg_count
        else:
            try:
                baseline = await self._page.evaluate(
                    "(sel) => document.querySelectorAll(sel).length",
                    self.profile.response_selector,
                ) or 0
            except Exception:
                baseline = 0

        logger.info(f"[{self.profile.name}] 等待回复（UI 信号驱动，{timeout}s 上限）... baseline_msg={baseline} (pre_sent={self._pre_send_msg_count})")

        interval = 0.5
        n_ticks = int(timeout / interval) + 1
        _wait_start = time.time()   # 看门狗计时基准
        # 【静默失效看门狗·加速】原阈值 75s 太长：实测通义等平台的网页会话一旦静默，
        # 每轮都要白等 75s 才判死，再叠加「换新会话 + 重发 13KB 上下文」的开销，
        # 单条任务轻松突破上层 300s 超时 → 用户侧看到「一直转圈、最终没有任何回复」。
        # 现降到 40s；同时包一层 on_thinking 记录「最后一次思考输出时间」——
        # 若模型还在持续吐思考过程（深度思考模式下 40s 不出正文是正常的），就不提前判死。
        _last_think_ts = [0.0]
        if on_thinking is not None:
            _orig_thinking = on_thinking

            def _thinking_watch(t):
                _last_think_ts[0] = time.time()
                return _orig_thinking(t)

            on_thinking = _thinking_watch
        _SILENT_LIMIT = 40
        gen_js = (
            "(sel) => { const els = document.querySelectorAll(sel);"
            " for (const el of els) { const r = el.getBoundingClientRect();"
            " if (r.width > 0 && r.height > 0) return true; } return false; }"
        )

        for _ in range(n_ticks):
            await asyncio.sleep(interval)
            _diag_tick += 1
            # ── 会话静默失效看门狗（按墙钟计时，放在 try 之外，任何异常都挡不住它）──
            # 实测豆包会话过长后：不报错、不产生新气泡、也不进入「生成中」状态，
            # 老逻辑会白等满 210s。这里只要 75s 仍一个字都没抓到就提前收工，
            # 让上层走「空回复重试 + 自动换新会话」。
            if not best_text and (time.time() - _wait_start) > _SILENT_LIMIT and \
                    (time.time() - _last_think_ts[0]) > 25:
                logger.warning(
                    f"[{self.profile.name}] 等待 {time.time() - _wait_start:.0f}s 仍未抓到任何回复正文"
                    f"（page_msg≈{total_count}），判定该网页会话已静默失效，提前结束等待"
                )
                return ""
            # 诊断：30s 仍未抓到文本 → dump 一次（放在 continue 之前，保证一定执行）
            if (not _mid_dumped) and _diag_tick == 60 and not best_text:
                _mid_dumped = True
                await self._diag_mid_dump()
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
                    incomplete_streak = 0
                    # 文本出现后重置思考超时计时（说明已进入回复阶段）
                    if hasattr(self, '_thinking_start_time'):
                        delattr(self, '_thinking_start_time')

                # 还没出字（含「提交中、尚未首个 token」的空窗）→ 视为仍在生成，继续等
                if not appeared or not text:
                    stable_count = 0
                    _no_appear_ticks += 1
                    # 【关键】75 秒连一个新气泡都没出现 ⇒ 基本可判定这个网页会话已经静默失效
                    # （实测豆包会话过长后就是这样：不报错、也不回复）。
                    # 早点返回空字符串，让上层走「空回复重试 + 自动换新会话」，别白等满 210 秒。
                    if _no_appear_ticks >= int(_SILENT_LIMIT / interval):
                        logger.warning(
                            f"[{self.profile.name}] 发送后 {_no_appear_ticks * interval:.0f}s "
                            f"内没有出现任何新消息气泡（page_msg≈{total_count}），"
                            f"判定该会话已静默失效，提前结束等待（上层会换新会话重试）"
                        )
                        return ""
                    continue
                _no_appear_ticks = 0

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
                    if (self._looks_incomplete(best_text)
                            and not _has_complete_protocol(best_text)
                            and incomplete_streak < 10):
                        incomplete_streak += 1
                        logger.info(
                            f"[{self.profile.name}] 文本疑似未完整（{len(best_text)}字，"
                            f"第{incomplete_streak}次），尾部={best_text[-90:]!r}，继续等待"
                        )
                        stable_count = 0
                        continue
                    if incomplete_streak >= 10:
                        logger.warning(
                            f"[{self.profile.name}] 「疑似未完整」已连续 {incomplete_streak} 次，"
                            f"强制按最终文本采纳（防止无限等待）: {len(best_text)} 字"
                        )
                    logger.info(f"[{self.profile.name}] 回复完成（UI 信号）: {len(best_text)} 字")
                    if on_thinking and last_thinking:
                        try:
                            on_thinking(last_thinking)
                        except Exception:
                            pass
                    return best_text

                # 兜底（无 generating_selector 时）：文本稳定 ~3s 判定完成
                if generating is None and stable_count >= 6:
                    if (self._looks_incomplete(best_text)
                            and not _has_complete_protocol(best_text)
                            and incomplete_streak < 10):
                        incomplete_streak += 1
                        logger.info(
                            f"[{self.profile.name}] 文本疑似未完整（稳定兜底，{len(best_text)}字，"
                            f"第{incomplete_streak}次），尾部={best_text[-90:]!r}"
                        )
                        stable_count = 0
                        continue
                    if incomplete_streak >= 10:
                        logger.warning(
                            f"[{self.profile.name}] 「疑似未完整」已连续 {incomplete_streak} 次，"
                            f"强制按最终文本采纳（稳定兜底）: {len(best_text)} 字"
                        )
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

            # 通义千问思考超时：如果思考内容持续增长且超时，尝试点击跳过按钮
            think_timeout = getattr(self.profile, 'thinking_timeout', 0) or 0
            skip_sel = getattr(self.profile, 'skip_thinking_selector', '') or ""
            if think_timeout > 0 and not appeared and last_thinking:
                thinking_start = getattr(self, '_thinking_start_time', None)
                if thinking_start is None:
                    self._thinking_start_time = time.time()
                    logger.info(f"[{self.profile.name}] 开始监控思考超时（{think_timeout}s）")
                elif time.time() - thinking_start > think_timeout:
                    logger.warning(f"[{self.profile.name}] 思考超时（{think_timeout}s），尝试跳过...")
                    if skip_sel:
                        try:
                            skip_btn = self._page.locator(skip_sel).first
                            if await skip_btn.count() > 0 and await skip_btn.is_visible():
                                await skip_btn.click()
                                logger.info(f"[{self.profile.name}] 已点击跳过按钮")
                                await asyncio.sleep(1)
                                # 继续等待回复而非返回
                                continue
                        except Exception as e:
                            logger.warning(f"[{self.profile.name}] 跳过思考按钮点击失败: {e}")
                    else:
                        # 无跳过按钮，直接继续等待（让回复正常完成）
                        logger.info(f"[{self.profile.name}] 思考超时但无跳过按钮，继续等待回复")

        # 超时兜底
        logger.warning(
            f"[{self.profile.name}] 等待回复超时（{timeout}s/{n_ticks} ticks），"
            f"已捕获 {len(best_text)} 字, appeared={appeared}, page_msg≈{total_count}"
        )
        # ---- 诊断：完全没抓到文本时，dump 页面候选 class 名，便于修正 response_selector ----
        if not best_text:
            try:
                _diag = await self._page.evaluate(
                    """() => {
                        const res = {url: location.href, classes: {}, tail: []};
                        const cnt = {};
                        document.querySelectorAll('*[class]').forEach(el => {
                            const c = el.className;
                            if (typeof c !== 'string' || !c) return;
                            c.split(/\\s+/).forEach(t => {
                                if (/message|chat|bubble|reply|answer|markdown|content|assistant|user|segment|item|md-/i.test(t)) {
                                    cnt[t] = (cnt[t] || 0) + 1;
                                }
                            });
                        });
                        res.classes = cnt;
                        const main = document.querySelector('main') || document.body;
                        res.tail = Array.from(main.querySelectorAll('div[class]')).slice(-50)
                                     .map(el => el.className).filter(Boolean);
                        return res;
                    }"""
                )
                _write_diag(self.profile.name, "timeout", _diag)
                logger.warning(f"[{self.profile.name}] DOM诊断: {str(_diag)[:2600]}")
            except Exception as _e:
                logger.warning(f"[{self.profile.name}] DOM诊断失败: {_e}")
        if on_thinking and last_thinking:
            try:
                on_thinking(last_thinking)
            except Exception:
                pass
        return best_text if best_text else None

    async def _diag_mid_dump(self):
        """诊断：回复期 dump 一次 DOM，用于定位真正的「回复节点」class（写入 jsonl，绕过日志缓冲）。"""
        try:
            _md = await self._page.evaluate(
                """() => {
                    const res = {url: location.href, classes: {}, listKids: [], strong: [], texts: []};
                    const cnt = {};
                    document.querySelectorAll('*[class]').forEach(el => {
                        const c = el.className;
                        if (typeof c !== 'string' || !c) return;
                        c.split(/\\s+/).forEach(t => {
                            if (/message|chat|bubble|reply|answer|markdown|content|assistant|md-box|item|segment/i.test(t)) {
                                cnt[t] = (cnt[t] || 0) + 1;
                            }
                        });
                    });
                    res.classes = cnt;
                    const lst = document.querySelector('[class*="message-list-"], [class*="list_items"], [class*="md-box-root"]');
                    if (lst) {
                        res.listKids = Array.from(lst.children).map(el => (typeof el.className === 'string' ? el.className : '').slice(0, 110));
                        const deep = lst.querySelectorAll('div[class]');
                        res.strong = Array.from(deep).slice(-25).map(el => (typeof el.className === 'string' ? el.className : '').slice(0, 110));
                    }
                    res.texts = Array.from(document.querySelectorAll('[class*="md-box-root"], [class*="content-"]'))
                                  .slice(-6).map(el => (el.innerText || '').slice(0, 160));
                    return res;
                }"""
            )
            _write_diag(self.profile.name, "mid30s", _md)
        except Exception as _e:
            logger.warning(f"[{self.profile.name}] 中期DOM诊断失败: {_e}")

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
        #    【关键修复】只有「未闭合标记出现在文本尾部」才算未完成。
        #    若正文中间夹带了 @@@@ 示例（模型复述指令/协议），奇偶判断会永久为真，
        #    导致 wait_response 卡满超时、母代理反复重试、用户看不到回复。
        open_marks = stripped.count("@@@@")
        if open_marks % 2 == 1:
            last_mark = stripped.rfind("@@@@")
            if last_mark >= len(stripped) - 60:   # 未闭合标记紧贴尾部 ⇒ 真·写到一半
                return True
        # 也检查只剩半个的情况（只以恰好 3 个 @ 结尾才是被截断的打开标记）
        # 【关键修复】原写法 `endswith("@@@") and not endswith("@@@@@@")` 会把
        # 「正常以 @@@@ 结尾的完整协议块」也判成未完成（endswith("@@@") 对 4 个 @ 同样为真），
        # 实测豆包每轮回复都因此白等 10 次 incomplete_streak（≈20 秒）。
        if stripped.endswith("@@@") and not stripped.endswith("@@@@"):
            return True

        # 2. 未闭合的 JSON（大括号/方括号不配对）
        #    只看尾部 60 字：真正流式写 JSON 时，未配对括号必然出现在结尾附近；
        #    放宽范围可避免正文里的示例 JSON 造成误判。
        tail = stripped[-60:] if len(stripped) > 60 else stripped
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
    
    async def spawn_child(self, headless: bool = None):
        """在同一浏览器实例里开一个新窗口（新 page），返回共享登录态的子管理器。

        与 browser.py 的 BrowserManager.spawn_child 同款设计（子代理支持的关键）：
        - 子管理器与母代理【共用同一个持久化上下文 / 同一个浏览器进程】
          （self._browser 是 launch_persistent_context 返回的 BrowserContext），
          因此 cookies / localStorage / 登录态 100% 继承，无需复制 profile，
          也不会有第二个浏览器实例抢 SingletonLock。
        - 子管理器只拥有自己的一个新 page，与母代理的 page 相互独立。
        - 之前 PlatformBrowserManager 没有 spawn_child → 子代理（task 工具）在非
          DeepSeek 平台一启动就 raise「母代理浏览器未就绪」；有了它，子代理可在
          任意平台（通义/豆包/元宝）复用当前平台浏览器跑通。
        """
        if self._browser is None:
            raise RuntimeError("母代理浏览器尚未启动，无法派生子窗口")

        child = PlatformBrowserManager(self.platform_key, headless=self.headless)
        # 与母代理共享底层资源（同一个浏览器进程 / 同一套登录态）
        child._playwright = self._playwright
        child._browser = self._browser          # 共享同一持久化上下文（= 同一浏览器）
        child._is_child = True                  # 子窗口：close() 只关自己的 page
        # 开一个新窗口（新 page），与母代理的 page 彼此独立
        child._page = await self._browser.new_page()
        logger.info(f"[{self.platform_key}] 已为子代理开新窗口（共享登录态，零复制）")
        return child

    async def close(self):
        """关闭浏览器"""
        # 子窗口 / 接管窗口：只关自己的 page，绝不动 owner 的浏览器进程 / playwright
        if getattr(self, "_is_child", False) or getattr(self, "_adopted", False):
            if self._page is not None:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = None
            logger.info(f"[{self.platform_key}] 子/接管窗口已关闭（owner 浏览器保持运行）")
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
        # 【同平台防双开】owner 关闭后摘除 registry 登记，后续 launch 可重新接管
        try:
            from agent_core import profile_lock as _pl
            _pl.release(self.user_data_dir, self)
        except Exception:
            pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass

        self._browser = None
        self._page = None
        self._playwright = None
        self._chrome_pids = None
        logger.info(f"[{self.profile.name}] 浏览器已关闭")



_WIN_CACTUS_HICON_BIG = None
_WIN_CACTUS_HICON_SM = None
_WIN_CACTUS_LOADED = False


def _win_apply_cactus_icon() -> int:
    """Windows 专属：把项目里的 __browser_cactus_icon.ico 设置到本机所有
    Chromium 顶层窗口（标题栏+任务栏图标）。
    关键修复：必须遍历所有 chrome.exe 进程 PID，并对每个进程的窗口发送
    WM_SETICON（SetClassLongPtr 只能改类图标，但 Chromium 多进程架构下每个
    子进程的窗口类可能被锁，必须用 WM_SETICON 才能保证生效）。"""
    global _WIN_CACTUS_HICON_BIG, _WIN_CACTUS_HICON_SM, _WIN_CACTUS_LOADED
    import sys as _sys
    if _sys.platform != "win32":
        return -1
    try:
        import win32gui
        import win32con
        import win32api
        import win32process
        ico = Path(__file__).resolve().parent.parent / "__browser_cactus_icon.ico"
        if not ico.exists():
            return 0
        # 缓存 HICON 句柄避免每次 LoadImage（巨幅提速）
        if not _WIN_CACTUS_LOADED:
            _WIN_CACTUS_HICON_BIG = win32gui.LoadImage(
                0, str(ico), win32con.IMAGE_ICON, 256, 256, win32con.LR_LOADFROMFILE
            )
            _WIN_CACTUS_HICON_SM = win32gui.LoadImage(
                0, str(ico), win32con.IMAGE_ICON, 16, 16, win32con.LR_LOADFROMFILE
            )
            if _WIN_CACTUS_HICON_BIG and _WIN_CACTUS_HICON_SM:
                _WIN_CACTUS_LOADED = True
        if not _WIN_CACTUS_LOADED:
            return 0
        hicon_big = _WIN_CACTUS_HICON_BIG
        hicon_sm = _WIN_CACTUS_HICON_SM

        # 1) 枚举所有可见顶层窗口，直接按「窗口所属进程 exe 路径」判断是不是
        #    Playwright Chromium —— 不再调用 tasklist（那是每轮 fork 一个进程，
        #    在常驻巡检里太贵）；QueryFullProcessImageNameW 只是内核查询，极便宜。
        import ctypes as _ct
        from ctypes import wintypes as _wt
        _k32 = _ct.windll.kernel32
        _u32 = _ct.windll.user32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        _exe_cache = {}

        def _is_pw_chromium(pid):
            if pid in _exe_cache:
                return _exe_cache[pid]
            ok = False
            h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                try:
                    buf = _ct.create_unicode_buffer(512)
                    size = _wt.DWORD(512)
                    if _k32.QueryFullProcessImageNameW(h, 0, buf, _ct.byref(size)):
                        p = buf.value.lower().replace("/", "\\")
                        ok = p.endswith("chrome.exe") and (
                            "playwright_browsers" in p or "ms-playwright" in p)
                finally:
                    _k32.CloseHandle(h)
            _exe_cache[pid] = ok
            return ok

        WM_SETICON = 0x0080
        ICON_BIG, ICON_SMALL = 1, 0
        SMTO_ABORTIFHUNG, SMTO_BLOCK = 0x0002, 0x0001

        hwnds = []
        def enum_cb(hwnd, results):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if not pid or not _is_pw_chromium(pid):
                    return True
                cls = win32gui.GetClassName(hwnd)
                # Chromium 所有窗口类：Chrome_WidgetWin_0/1/2...
                if cls.startswith("Chrome_WidgetWin_") or cls.startswith("Chrome_RenderWidget") or "Cef" in cls:
                    results.append(hwnd)
            except Exception:
                pass
            return True
        win32gui.EnumWindows(enum_cb, hwnds)

        # 2) 对每个窗口发送 WM_SETICON（per-window，强制立即生效）。
        #    · 已经是我们这张图的窗口直接跳过（幂等，让常驻巡检几乎零开销）。
        #    · 用 SendMessageTimeout + SMTO_ABORTIFHUNG，避免目标进程卡住时把
        #      常驻线程一起拖死。
        n = 0
        for hwnd in hwnds:
            try:
                if win32gui.SendMessageTimeout(
                        hwnd, 0x007F, ICON_BIG, 0, SMTO_ABORTIFHUNG | SMTO_BLOCK, 500
                )[1] == hicon_big:
                    continue                      # 已经是仙人球，免打扰
                win32gui.SendMessageTimeout(hwnd, WM_SETICON, ICON_BIG, hicon_big,
                                            SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000)
                win32gui.SendMessageTimeout(hwnd, WM_SETICON, ICON_SMALL, hicon_sm,
                                            SMTO_ABORTIFHUNG | SMTO_BLOCK, 1000)
                n += 1
            except Exception:
                pass
        return n
    except Exception as e:
        logger.debug(f"[_cactus_icon] 设置失败: {type(e).__name__}: {e}")
        return 0


# ── 仙人球图标常驻守护线程 ─────────────────────────────────────────────
# 【2026-09-21 真机根因修复】
# 现象：后端日志明明打了「图标设置成功 HWND=xxxx」，用户看到的浏览器图标却仍是
#       Chromium 原图标。
# 根因：Chromium 的窗口是**动态创建**的。launch 之后先冒出来的往往是一个临时窗口
#       （例如「要恢复页面吗？」对话框），真正的主窗口要晚几秒才出现。
#       而旧实现（browser.py 的 _apply_window_icon_once / 本模块的
#       _apply_cactus_icon_loop）**只要给任意一个窗口设置成功就立刻 return/break**，
#       于是图标被贴到了那个临时窗口上，主窗口永远没人管 → 用户看到的还是 Chrome。
# 修复：不再依赖「一次性 + 成功即停」，改成常驻守护线程持续巡检，
#       任何后出现的 Chromium 窗口都会被补上仙人球。
_CACTUS_WATCHER_STARTED = False


def _cactus_icon_watcher_loop():
    while True:
        try:
            _win_apply_cactus_icon()
        except Exception:
            pass
        time.sleep(2.0)


def _start_cactus_icon_watcher():
    """启动常驻图标守护线程（幂等；非 Windows 直接跳过）。"""
    global _CACTUS_WATCHER_STARTED
    if _CACTUS_WATCHER_STARTED:
        return
    import sys as _sys
    if _sys.platform != "win32":
        return
    _CACTUS_WATCHER_STARTED = True
    try:
        import threading
        threading.Thread(target=_cactus_icon_watcher_loop,
                         name="cactus-icon-watcher", daemon=True).start()
        logger.info("[_cactus_icon] 常驻图标守护线程已启动（每 2s 巡检 Chromium 窗口）")
    except Exception as e:
        _CACTUS_WATCHER_STARTED = False
        logger.debug(f"[_cactus_icon] 守护线程启动失败: {e}")


# 模块被 import 即启动 —— terminal.py 通过 exec 加载 terminal.pyc，pyc 内部会
# import 本模块，所以这里能覆盖「后端启动就拉起浏览器」的那条路径。
try:
    _start_cactus_icon_watcher()
except Exception:
    pass


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

class LoginRequiredError(RuntimeError):
    """平台掉登录了：不要再重试，直接把「请扫码登录」如实报给用户。"""


class SecurityVerificationRequiredError(RuntimeError):
    """平台触发了【反自动化图形验证】（图片拖拽/滑块/选图 CAPTCHA）。

    这种情况不是「会话静默失效」，也绝不是空回复能自愈的：它是平台风控把我
    的高频重发（6.8KB 协议块 × 多次 + 自动开新会话）判定为机器人后弹出的验证墙。
    验证墙需要【人】在浏览器里手动完成一次，Agent 侧继续自动重试只会：
      - 把验证墙越刷越频繁（每次开新会话 + 重发整段协议都算一次机器行为）；
      - 让「等待 40s 无回复」空转 4 轮 × 210s，用户侧看到「一直转圈没反应」。
    所以一旦命中验证墙，立即如实上抛，停止空转，告诉用户去浏览器里手动过一次验证。
    """


# 平台页面掉登录时， scrape 回来的「回复」其实就是登录墙上的字
_LOGIN_WALL_TOKENS = (
    "请使用微信扫描二维码登录", "请扫描二维码登录", "扫码登录", "扫码默认已阅读",
    "未登录", "登录后使用", "请先登录", "请登录", "Sign in to continue",
)

# 平台风控「反自动化图形验证」墙上的特征词（豆包实测：图片拖拽选择类 CAPTCHA）。
# 命中任意一条即判定为验证墙，而不是「会话静默失效」。
_SECURITY_WALL_TOKENS = (
    "请选择所有符合", "符合上述描述", "符合上图", "并拖拽到", "拖拽到下方",
    "拖拽到这里", "按住滑块", "向右滑动", "请完成验证", "验证一下",
    "请拖动下方", "滑动验证", "点击验证", "完成拼图", "拼图验证",
)

# 平台自身的「工具不存在」报错行（实测通义 chat.qwen.ai）：
# 模型在网页端尝试用【平台原生】function calling 调 web_fetch / done 等，
# 平台回一行「Tool web_fetch does not exists.」并渲染进回复气泡里。
# wait_response 会把它当正文抓回来 → 最终回复开头糊 4 行英文报错给用户看。
_PLATFORM_TOOL_ERR_RE = re.compile(r"^Tool\s+[\w.\-]+\s+does\s+not\s+exists?\s*\.?\s*$", re.I)


def strip_platform_noise_lines(text: str) -> str:
    """剔掉平台页面混进回复正文里的报错行（不影响协议块与真正的正文）。"""
    if not text:
        return text
    try:
        kept = [ln for ln in str(text).splitlines()
                if not _PLATFORM_TOOL_ERR_RE.match(ln.strip())]
        return "\n".join(kept).strip()
    except Exception:
        return text


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
        # 【修复】整个会话只用一个会话 JSON 文件。以前每次保存都按时间戳新建文件，
        # 于是「一次任务 = 几十条历史记录」，历史抽屉被刷屏（实测 442 条里绝大多数是同
        # 一个任务的重复落盘）。固定住路径后 _record_task 会按 file 去重、原地更新。
        self._conv_file_path: str = ""
        self._system_prompt: str = ""
        self._thinking_mode = False
        self._logged_in = True             # 由外部 check_login 决定，这里默认放行
        self._on_event = None              # 思考过程等事件回调 (event_type, data) -> None
        self._last_thinking = ""
        self._context_exhausted = False    # 上下文耗尽检测标志
        # 【原生多轮修复】播种态必须挂在【持久】的 bm（PlatformBrowserManager）上，
        # 不能只挂在 session 上。GUI 每条用户消息都会 new 一个 PlatformSession，
        # 但 豆包 的浏览器 context（/chat/<id> 里已有的对话）是持久的。若播种态
        # 只在 session 上，新 session 会把 6.8KB 协议重新砸进 豆包【正在进行的对话】
        # 里 → 豆包卡壳 → 40s 无回复 → 假「静默失效」→ 自愈又跳回空白 /chat/ 把
        # 原生多轮上下文整个销毁。挂到 bm 上后：后续消息的 session 读到「已播种」，
        # 只发轻量提醒 + 用户原话，靠 豆包 网页同一 /chat/<id> 的对话记忆续上下文。
        self._proto_seeded = getattr(platform_bm, "_native_multi_turn_seeded", False)
        import logging as _lg
        _lg.getLogger(__name__).debug(
            f"[原生多轮诊断] PlatformSession(bm_id={id(platform_bm)}) "
            f"init读到 _native_multi_turn_seeded={self._proto_seeded} "
            f"platform={platform_bm.profile.name if getattr(platform_bm,'profile',None) else '?'}"
        )

    # ---- 与 DeepSeekSession 对齐的属性/方法 ----
    @property
    def thinking_mode(self) -> bool:
        return self._thinking_mode

    def toggle_thinking(self):
        self._thinking_mode = not self._thinking_mode
        logger.info(f"[{self._bm.profile.name}] 思考模式: {self._thinking_mode}")

    async def set_deep_think(self, enable: bool) -> bool:
        """开启/关闭深度思考：实际点击平台 UI 的「深度思考」开关（不再只是设内存标志）。

        返回值：True 表示确实切换成功；False 表示该平台没有可用开关（上层会
        如实告知模型，而不是谎报「已开启」——谎报会让模型以为处于深度思考态，
        实测豆包还会因为去点「快速/深入研究」模式按钮而把页面点坏）。
        """
        self._thinking_mode = enable
        try:
            ok = await self._bm.enable_thinking(enable)
            if ok:
                logger.info(f"[{self._bm.profile.name}] 深度思考已{'开启' if enable else '关闭'}")
                return True
            logger.warning(
                f"[{self._bm.profile.name}] 深度思考开关不可用"
                f"（该平台网页端未提供，或需登录后才显示）"
            )
            return False
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 深度思考切换失败: {e}")
            return False

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

    # 【轻量协议提醒卡】后续轮次发给网页端的「格式提醒」——只几百字，不再是 6.8KB。
    # 网页（豆包/通义/元宝）在同一会话里自己持有完整对话记忆（含首轮喂进去的
    # 工具列表 + 核心指令），所以后续轮次只需一句「继续用 @@@@ 协议」的轻提醒 +
    # 用户原话即可。重发 6.8KB 是喂死网页端、逼出假「静默失效」的根因，故砍掉。
    _COMPACT_PROTOCOL_CARD = (
        "【格式提醒】本会话继续：你只能用 @@@@ JSON 协议回复，格式 "
        "@@@@{\"tool\":\"工具名\",\"params\":{...},\"id\":\"1\"}@@@；"
        "从首轮已列出的工具集里选（docx_create / pptx_create / "
        "file_write / file_edit / file_read / browser_search / task / done 等），"
        "不要用网页内建功能生成文件。全部做完后调用 done 收尾。"
    )

    def _build_context(self, new_user_text: str) -> str:
        """把上下文拼成单条文本发送给平台。

        【原生多轮修复·关键】区分首轮 vs 后续轮：
          - 首轮（本浏览器会话还没喂过协议，_proto_seeded 为 False）：
            发完整系统提示词 + 近期历史 + 格式提醒 + 用户原话。这是把「工具列表 +
            核心指令」一次性播种进网页，让网页端的模型知道协议。
          - 后续轮（已播种，_proto_seeded 为 True）：只发「轻量格式提醒 + 用户原话」，
            靠网页在同一会话里的原生多轮记忆续上下文，**不再重打包 ~6.8KB 系统提示词**。

        之前每轮都整段重发 6.8KB 系统提示词，网页端（尤其豆包）吃不下这种
        「每轮重新塞满」的输入 → 喂到卡壳 → 40s 不出正文 → 被误判成「会话静默失效」
        → 又换新会话再塞 6.8KB → 死循环。改用原生多轮（网页自持上下文）后，
        短问题就是短输入，多轮才真正能续上。
        """
        lines = []
        import logging as _lg2
        _seed_now = getattr(self, "_proto_seeded", False)
        _bm_id = id(self._bm)
        if not _seed_now:
            # ── 首轮：完整播种 ──
            _lg2.getLogger(__name__).debug(
                f"[原生多轮分支] 走【完整播种】分支 sess_id={id(self)} bm_id={_bm_id} "
                f"sess_flag={_seed_now} bm_flag={getattr(self._bm,'_native_multi_turn_seeded','<unset>')} "
                f"sys_len={len(self._system_prompt)} text={new_user_text[:24]!r}"
            )
            MAX_RECENT = 6
            if self._system_prompt:
                lines.append(f"[系统指令]\n{self._system_prompt}")
            user_msgs = [m for m in self._messages if m["role"] == "user"]
            recent_user = user_msgs[-MAX_RECENT:] if len(user_msgs) > MAX_RECENT else user_msgs
            for m in recent_user:
                lines.append(f"[用户] {m['content']}")
            recent_assistant = [m for m in self._messages if m["role"] == "assistant"]
            for m in recent_assistant[-2:]:
                content = m["content"] if len(m["content"]) < 2000 else m["content"][:2000]
                lines.append(f"[助手] {content}")
            lines.append(self._compact_format_reminder())
            self._proto_seeded = True
            # 【关键】播种态必须回写到持久的 bm，否则下一条 GUI 消息 new 出的
            # 新 session 读不到「已播种」，会把 6.8KB 再次砸进 豆包 正在进行的
            # /chat/<id> 对话里 → 卡壳 → 假静默失效 → 自愈跳回空白页毁掉原生多轮。
            try:
                self._bm._native_multi_turn_seeded = True
            except Exception:
                pass
        else:
            # ── 后续轮：只轻量提醒，吃网页原生多轮记忆 ──
            _lg2.getLogger(__name__).debug(
                f"[原生多轮分支] 走【轻量提醒】分支 sess_id={id(self)} bm_id={_bm_id} "
                f"sess_flag={_seed_now} text={new_user_text[:24]!r}"
            )
            lines.append(self._COMPACT_PROTOCOL_CARD)
        lines.append(f"[用户] {new_user_text}")
        return "\n\n".join(lines)

    def _compact_format_reminder(self) -> str:
        """首轮随完整协议一起发的「格式提醒」（放在最末、紧贴任务前一行）。

        【关键·弱模型适配】网页聊天平台的模型经常忽略埋在长提示词开头的格式要求，
        直接用自然语言回答（实测豆包就会回「……我是仙人掌 Agent…… done ()」，
        完全没有 @@@@ 协议块），导致协议解析失败 → 反复纠正空转。
        解决：把最精炼的格式要求紧贴在实际任务【前一行】重复一次。
        """
        return (
            "[格式提醒·必须遵守] 你只能用下面这种 @@@@ JSON 格式回复，不要用自然语言描述你要做什么：\n"
            "@@@@\n"
            '{"tool":"工具名","params":{...},"id":"1"}\n'
            "@@@@\n"
            "例如写文件：@@@@\n"
            '{"tool":"file_write","params":{"path":"a.txt","content":"你好"},"id":"1"}\n'
            "@@@@\n"
            "任务全部做完后，用：@@@@\n"
            '{"tool":"done","params":{},"id":"9"}\n'
            "@@@@"
        )

    # 单个网页会话累计「发送字符数」上限：超过就自动开新会话。
    # 实测豆包：每轮都要把 6.6k~7.4k 字符的系统提示词整段重发进同一个会话，
    # 一个会话里发到第 3 轮（累计约 2 万字符）时网页端就【静默停止返回任何内容】——
    # 不报错、也没有新气泡，表现为 wait_response 卡满 210s。只能靠「换新会话」自愈。
    # 注意：这里统计的是【真正发出去的完整上下文长度】，不是 _messages 里那些短文本，
    # 否则永远达不到阈值（实测踩过这个坑）。
    # 【修复·重要】原阈值 14000 是致命 bug：我们每轮都要重发完整系统提示词（约 7KB），
    # 所以「累计发送字符」在第 2 轮就超过 14000 → 每两轮就换一次新会话，
    # 模型永远拿不到连续上下文，只好把每条请求当成全新任务理解
    # （实测豆包因此把「生成 pptx」当成本地办公需求，直接跳进「豆包办公」沙箱自己生成文件，
    #  完全不理我们的 @@@@ 工具协议）。
    # 现改为：以【本会话已进行的轮数】为主判据，字符数只作兜底护栏；
    # 网页端真的静默停止时由「空回复自愈」路径负责换会话，不必靠瞎猜字符数。
    _RESET_CTX_CHARS = 40000
    _RESET_TURNS = 10

    async def send(self, text: str, attachments: List[str] = None,
                   internal: bool = False) -> str:
        """发送并等待回复（接口与 DeepSeekSession.send 一致）

        internal=True 表示这是 Agent 的内部轮（工具结果回传、协议纠正、
        继续指令等），不是用户说的话 —— 会被标记为 internal，不写进对话历史文件，
        避免历史记录里塞满 [系统] 工具…执行结果 这类噪音（用户看到的就是「一团乱」）。
        """
        # ── 本会话首轮预检登录态 ──
        # 不做这层预检的话，未登录平台要先走「点击输入框 15s 超时 + 空等回复 40s」
        # 才被发现，用户干等近 90 秒才看到一句「请先登录」。
        # （check_login 内部会在页面骨架屏阶段等待稳定再判定，避免误判成已登录。）
        if not getattr(self, "_login_prechecked", False):
            self._login_prechecked = True
            try:
                if not await asyncio.wait_for(self._bm.check_login(), timeout=40):
                    self._login_required = True
                    raise LoginRequiredError(
                        f"[{self._bm.profile.name}] 平台未登录，请先在打开的浏览器窗口里登录，"
                        f"再重新发任务")
                logger.info(f"[{self._bm.profile.name}] 首轮登录预检通过")
            except LoginRequiredError:
                raise
            except Exception:
                pass   # 预检查不动就按原流程走，不因预检失败阻断任务

        # ── 掉登录短路：上一次已经确认未登录后，每条指令先做一次便宜的登录态检查 ──
        # 不做这层短路的话，未登录时每条指令都要走「等输入框 15s 超时」或
        # 「等回复 210s 超时」才被发现，用户连发 5 条就要干等十几分钟，
        # 而且每条都只看到一句看不懂的 Playwright 报错。
        if getattr(self, "_login_required", False):
            try:
                if await asyncio.wait_for(self._bm.check_login(), timeout=15):
                    self._login_required = False   # 用户已登录，恢复正常工作
                    logger.info(f"[{self._bm.profile.name}] 检测到已完成登录，恢复任务执行")
            except Exception:
                pass
            if getattr(self, "_login_required", False):
                raise LoginRequiredError(
                    f"[{self._bm.profile.name}] 平台未登录，请先在打开的浏览器窗口里登录，"
                    f"再重新发任务")

        # 上下文耗尽检测
        if getattr(self, '_context_exhausted', False):
            recovered = await self.auto_recover_from_exhaustion()
            if recovered:
                logger.info(f"[{self._bm.profile.name}] 上下文已自动恢复，继续任务")
            else:
                logger.warning(f"[{self._bm.profile.name}] 上下文恢复失败，继续使用当前会话")
                self._context_exhausted = False

        # ── 主动自愈 1：上一轮一个字都没抓到（会话可能已静默失效）→ 换新会话再试 ──
        if getattr(self, "_last_reply_empty", False):
            self._last_reply_empty = False
            logger.warning(f"[{self._bm.profile.name}] 上一轮未抓到回复，自动开启新会话后重试")
            await self._safe_new_conversation()

        # ── 主动自愈 2：本会话轮数偏多 / 累计发送过长 → 换新会话（兜底护栏）──
        full_context = self._build_context(text)
        _sent_before = getattr(self, "_thread_sent_chars", 0)
        _turns_before = getattr(self, "_thread_turns", 0)
        if _turns_before >= self._RESET_TURNS or \
                (_sent_before and (_sent_before + len(full_context)) > self._RESET_CTX_CHARS):
            logger.warning(
                f"[{self._bm.profile.name}] 本会话已进行 {_turns_before} 轮、"
                f"累计发送 {_sent_before} 字符（轮数上限 {self._RESET_TURNS} / "
                f"字符上限 {self._RESET_CTX_CHARS}），开启新会话以免网页端静默卡死"
            )
            await self._safe_new_conversation()   # 自愈：保留用户原话
            self._thread_sent_chars = 0
            self._thread_turns = 0
            full_context = self._build_context(text)   # 新会话下重建上下文
        self._thread_sent_chars = _sent_before + len(full_context)
        self._thread_turns = _turns_before + 1

        self._messages.append({"role": "user", "content": text, "internal": bool(internal)})

        # 发送（含附件）
        try:
            await self._bm.send_message(full_context, attachments=attachments)
        except LoginRequiredError:
            raise
        except Exception as e:
            # 发送失败最常见的真实原因不是「页面卡了」，而是掉登录了：
            # 登录墙/扫码页根本就没有可输入的对话框，于是 fill/click 一直等到超时。
            # 这种情况如果照原样把 Playwright 的 Call log 整坨抛出去：
            #   · 用户看到的是一堆 DOM 选择器堆栈，完全不知道该去登录；
            #   · 母代理会当成「普通调用失败」按协议反复重试，每轮再白等 15s+。
            # 所以发送失败后先查一次登录态，掉登录就抛 LoginRequiredError，
            # 由上层统一转成「[需要登录] 请先登录」这样的明确提示。
            try:
                _still = await asyncio.wait_for(self._bm.check_login(), timeout=15)
            except Exception:
                _still = True   # 查不动就按原样报错，不误判成未登录
            if not _still:
                self._login_required = True   # 后续指令直接短路，别再白等 15s
                raise LoginRequiredError(
                    f"[{self._bm.profile.name}] 平台未登录（找不到可输入的对话框），"
                    f"请先在打开的浏览器窗口里登录，再重新发任务") from e
            # 非登录问题：错误信息截断，别把几十行 Call log 塞进聊天窗口
            _err = str(e)
            if len(_err) > 300:
                _err = _err[:300] + " …（已截断完整 Call log）"
            raise RuntimeError(f"[{self._bm.profile.name}] 发送失败: {_err}")

        # 等待回复（第三方平台流式较慢，给 120s），实时抓取「思考过程」
        # 第三方平台（千问/豆包/元宝）带「深度思考」时输出较慢，120s 常在收尾处被截断，
        # 放宽到 210s，避免「回复被腰斩 → 无 @@@@ 协议 → 母代理反复重试」。
        response = await self._bm.wait_response(
            timeout=210,
            on_thinking=self._emit_thinking,
            thinking_selector=self._bm.profile.thinking_selector,
        )
        # ── 网页交互留痕：把这一轮的页面整页截图存下来，方便事后核对 ──
        # 出问题（空回复/被豆包办公劫持/渲染成状态行）时，光看日志是猜，
        # 看截图一眼就能看出来。由 XRZ_UI_DUMP=1 打开，默认关闭不影响速度。
        await self._ui_dump("reply")
        # 确保最终思考内容已上报（避免末尾事件被缓冲截断）
        if self._last_thinking and self._on_event:
            try:
                self._on_event("ai_thinking", {"text": self._last_thinking})
            except Exception:
                pass
        if not response:
            # ── 反自动化验证墙检测：豆包等平台的图形拖拽 CAPTCHA，md-box-root 读不到，
            #    会表现为「40s 无正文」→ 老逻辑误判成「会话静默失效」，一路开新会话重发。
            #    命中验证墙就如实上抛，停止空转，让用户去浏览器里手动过一次验证。
            _wall_hit = await self._detect_security_wall()
            if _wall_hit:
                self._ui_dump("security-wall")
                raise SecurityVerificationRequiredError(
                    f"[{self._bm.profile.name}] 平台弹出反自动化图形验证（图片拖拽/滑块类 CAPTCHA）。"
                    f"这是平台风控把本程序的高频自动操作判定为机器人所致，网页端需要【手动】"
                    f"在打开的浏览器窗口里完成一次验证后才能继续。请勿反复重发，越刷验证墙越频繁。"
                    f"请去浏览器窗口里手动过一遍图片验证，过完再重新发任务。"
                )
            # ── 掉登录检测：页面根本不是一个可对话的会话，再重试 3 轮 × 210s 毫无意义 ──
            try:
                if not await self._bm.check_login():
                    self._login_required = True   # 短路后续指令，避免每条都白等超时
                    raise LoginRequiredError(
                        f"[{self._bm.profile.name}] 平台未登录（页面显示登录/扫码入口），"
                        f"请先在打开的浏览器窗口里登录，再重新发任务")
            except LoginRequiredError:
                raise
            except Exception:
                pass
            # 【关键修复】这里以前会把空回复改写成"（未收到回复）"再返回，
            # 结果母代理以为「模型说了 7 个字」，于是走「没遵守协议」的纠正分支，
            # 在同一个已失效的会话里反复重试（每次白等 210s）。
            # 正确做法：如实返回空字符串，让母代理走「空回复重试」，并在下一轮换新会话。
            logger.warning(
                f"[{self._bm.profile.name}] 本轮未抓到任何回复正文"
                f"（会话可能已静默失效），按空回复上报，下一轮将自动换新会话"
            )
            self._last_reply_empty = True
            # 【关键修复】不要把「（未收到回复）」写进对话历史。
            # 它是给人看的占位符，一旦进了 _messages：
            #   1) 会被写进会话文件，下次回看任务时它作为"AI 的回复"被渲染出来；
            #   2) 会被 _visible_messages 取到，混进「🧠 思考过程」块的开头，
            #      界面上就出现「（未收到回复） The user sent ...」这种鬼东西。
            # 空回复就如实留空，让上层走重试/报错分支，不要污染上下文。
            self._maybe_save_conversation()
            await self._ui_dump("empty-reply")
            return ""
        self._messages.append({"role": "assistant", "content": response})
        logger.info(f"[{self._bm.profile.name}] 对话完成，历史 {len(self._messages)} 条")
        # 抓回来的「回复」里若混着风控验证墙字样（部分平台把 CAPTCHA 提示塞进正文）
        # → 同样如实停下，别当成正常回复继续空转。
        if any(k in (response or "") for k in _SECURITY_WALL_TOKENS):
            await self._ui_dump("security-wall")
            raise SecurityVerificationRequiredError(
                f"[{self._bm.profile.name}] 平台触发了反自动化图形验证（回复里出现验证提示）。"
                f"请去打开的浏览器窗口里手动过一次验证，再重新发任务。"
            )
        # 抓回来的「回复」其实是登录墙 → 直接报未登录，别让母代理反复纠正空转
        if any(k in (response or "") for k in _LOGIN_WALL_TOKENS):
            self._login_required = True   # 同上：后续指令直接短路，别再空转
            raise LoginRequiredError(
                f"[{self._bm.profile.name}] 平台未登录（回复里是登录/扫码提示），"
                f"请先在打开的浏览器窗口里登录，再重新发任务")

        # ===== 上下文耗尽检测与自动恢复 =====
        self._check_context_exhausted(response)

        # ===== 自动持久化（修复「历史记录是坏的」：第三方平台对话也要落盘）=====
        self._maybe_save_conversation()

        return response

    # ── 网页交互留痕（XRZ_UI_DUMP=1 打开）──
    # 每轮把平台页面**直接打印成 PDF**，落到 ui_dumps/<平台>/，出错时额外打一张。
    # 光看日志只能猜「网页上到底发生了什么」，翻 PDF 一眼就能看出来
    # （豆包跳进办公沙箱、页面渲染成状态行、空回复……全是肉眼可见的）。
    # 实测有头 Chromium 的 page.pdf() 可用（_test_pdf_headed.py 验证过）。
    @staticmethod
    def _ui_dump_enabled() -> bool:
        import os as _os
        return _os.environ.get("XRZ_UI_DUMP") == "1"

    async def _detect_security_wall(self) -> bool:
        """检测平台是否弹出了反自动化图形验证墙（豆包图片拖拽 / 滑块 CAPTCHA）。

        md-box-root（AI 正文选择器）读不到验证墙 → 空回复路径里必须先判断它，
        否则会把「被风控锁住」误判成「会话静默失效」，一路开新会话重发 6.8KB 协议块，
        越刷验证墙越频繁。命中特征词即返回 True，让上层如实停下、提示用户手动过验证。

        两个实测坑，必须都处理：
          1) 验证组件常挂在【独立 frame/iframe】里，主文档 body.innerText 抓不到 →
             必须遍历 page 的所有 frame。
          2) 豆包页面用【CJK 兼容汉字】渲染（如 上⽂ 的 ⽂=U+2F52 而非 文=U+4E0B），
             直接子串匹配会漏 → 先归一：去掉所有空白，并把 CJK 兼容区字符剔除后再匹配
             （用不含兼容区字符的『标准字特征词』做匹配，命中即判墙）。
        """
        page = getattr(self._bm, "_page", None)
        if not page:
            return False
        try:
            frames = list(getattr(page, "frames", []) or [page])
        except Exception:
            frames = [page]
        raw_parts = []
        for fr in frames:
            try:
                t = await fr.evaluate("() => (document.body && document.body.innerText) || ''")
                if t:
                    raw_parts.append(str(t))
            except Exception:
                continue
        joined = "\n".join(raw_parts)
        # 归一化：去空白；再把 CJK 兼容区/康熙部首区字符剔除，让『标准字特征词』能命中。
        import re as _re
        no_ws = _re.sub(r"\s+", "", joined)
        normalized = _re.sub(r"[\u2e80-\u2fff\uf900-\ufaff]", "", joined)
        # 在『去空白』与『去兼容区』两份归一化文本里分别匹配标准字特征词。
        candidates = {
            "拖拽到": ("拖拽到", "拖拽到"),
            "请选择所有": ("请选择所有", "请选择所有"),
            "按住滑块": ("按住滑块", "按住滑块"),
            "向右滑动": ("向右滑动", "向右滑动"),
            "滑动验证": ("滑动验证", "滑动验证"),
            "拖动下方": ("拖动下方", "拖动下方"),
            "请完成验证": ("请完成验证", "请完成验证"),
            "完成拼图": ("完成拼图", "完成拼图"),
            "拼图验证": ("拼图验证", "拼图验证"),
            "符合上述描述": ("符合上述描述", "符合上图", "符合相关"),
            "符合上图": ("符合上图", "符合上述描述"),
        }
        hit = None
        for label, (primary, *alts) in candidates.items():
            needles = [primary] + list(alts)
            for blob in (no_ws, normalized):
                if any(n and n in blob for n in needles):
                    hit = label
                    break
            if hit:
                break
        import logging as _lg3
        if hit:
            _lg3.getLogger(__name__).warning(
                f"[{getattr(self._bm.profile, 'name', '?')}] 检测到反自动化验证墙（命中特征「{hit}」），"
                f"页面 frames={len(frames)} 总字数≈{len(joined)}，停止空转、如实上抛"
            )
        else:
            _lg3.getLogger(__name__).info(
                f"[{getattr(self._bm.profile, 'name', '?')}] 空回复但【未】命中验证墙特征"
                f"（frames={len(frames)} 正文≈{len(joined)}字），按普通静默处理"
            )
        return bool(hit)

    async def _ui_dump(self, tag: str):
        if not self._ui_dump_enabled():
            return
        try:
            page = getattr(self._bm, "_page", None)
            if not page:
                return
            from pathlib import Path as _P
            from datetime import datetime as _dt
            base = _P(r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\ui_dumps")
            d = base / (getattr(self._bm.profile, "platform", None) or "unknown")
            d.mkdir(parents=True, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            try:
                f = d / f"{ts}_{tag}.pdf"
                await page.pdf(path=str(f), format="A4", print_background=True)
            except Exception:
                # 万一某个页面 pdf() 不灵，退回截图，保证留痕不丢
                f = d / f"{ts}_{tag}.png"
                await page.screenshot(path=str(f), full_page=True)
            logger.info(f"[UI留痕] {f}")
        except Exception as e:
            logger.warning(f"[UI留痕] 留痕失败（不影响任务）: {e}")

    async def _safe_new_conversation(self):
        """尽量安全地开一个新会话（失败不影响主流程）。

        注意：这是【自愈】路径，用户的任务没变，必须把用户原话带过去
        （keep_user_task=True），否则历史记录里这条任务就只剩 AI 的回答了。
        """
        try:
            await self.start_new_conversation(keep_user_task=True)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.warning(f"[{self._bm.profile.name}] 开启新会话失败: {e}")
            _carry = [dict(m) for m in self._messages
                      if m.get("role") == "user" and not m.get("internal")]
            try:
                self._messages.clear()
                self._messages.extend(_carry)
            except Exception:
                pass

    def _check_context_exhausted(self, response: str):
        """检测 AI 回复是否包含上下文耗尽信号"""
        if getattr(self, '_context_exhausted', False):
            return
        exhaust_signals = [
            "上下文已超出", "超出上下文", "context exceeded",
            "too long", "token limit", "maximum context",
            "抱歉，上下文", "抱歉超出", "抱歉，对话过长",
        ]
        resp_lower = (response or "").lower()
        for sig in exhaust_signals:
            if sig in resp_lower:
                self._context_exhausted = True
                logger.warning(f"[{self._bm.profile.name}] 检测到上下文耗尽信号: {sig}")
                return
        if response and response.strip().startswith("抱歉") and len(response.strip()) < 100:
            self._context_exhausted = True
            logger.warning(f"[{self._bm.profile.name}] 检测到疑似上下文耗尽: {response[:60]}")

    async def auto_recover_from_exhaustion(self) -> bool:
        """上下文耗尽后自动恢复"""
        if not getattr(self, '_context_exhausted', False):
            return False
        self._context_exhausted = False
        try:
            self._messages.clear()
            if self._bm and getattr(self._bm, "new_session", None):
                await self._bm.new_session()
                await asyncio.sleep(1)
            # 注：PlatformSession 的 _messages 是 list of dict，DeepSeekSession 是 list of Message
            # 这里只记录恢复事件，续传逻辑由外部（commander）处理
            return True
        except Exception as e:
            logger.error(f"[{self._bm.profile.name}] 上下文恢复失败: {e}")
            return False

    # ---- 对话历史持久化（与 DeepSeekSession 对齐）----
    def _maybe_save_conversation(self):
        """每轮都持久化（修复：以前「每 5 条用户消息才存一次」导致单轮任务从不落盘；
        而自愈清空上下文后 user_count 变 0 又退化成每轮新建文件，把历史刷爆）。
        会话 JSON 路径固定复用，每轮覆盖写同一个文件，既不漏存也不刷屏。"""
        self._do_save_conversation()

    def _do_save_conversation(self):
        try:
            from .session import get_conversation_history, Message, _task_conv_dir
            hist = get_conversation_history()
            msgs = [Message(role=m["role"], content=m["content"])
                    for m in self._visible_messages()]
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

    def _visible_messages(self) -> list:
        """过滤掉内部轮（工具结果/协议纠正）和系统提示词，只留用户真实说的话 + AI 回复。"""
        return [m for m in self._messages
                if not m.get("internal") and m.get("role") != "system"]

    def _save_conv_json(self, file_path: str = None) -> str:
        """方案二：把消息落盘成平台 JSON（含 URL，便于交叉校验）。

        只写用户真实对话（_visible_messages）：内部工具结果轮不落盘，
        历史记录里才不会出现 [系统] 工具 xxx 执行结果 之类的噪音。
        """
        from .session import _task_conv_dir, _record_task
        if file_path is None:
            # 同一个会话复用同一个文件：历史记录里一个任务只占一条，且标题始终能取到用户原话
            if not getattr(self, "_conv_file_path", ""):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._conv_file_path = str(
                    _task_conv_dir() / f"conv_{self._bm.profile.platform}_{ts}.json")
            file_path = self._conv_file_path
        else:
            self._conv_file_path = file_path
        data = {
            "platform": self._bm.profile.platform,
            "url": self.get_current_url(),
            "messages": [{"role": m["role"], "content": m["content"]}
                         for m in self._visible_messages()],
        }
        Path(file_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 把这次对话登记为【一个独立任务】（标题=首条用户消息）
        _record_task(self._bm.profile.platform, file_path,
                     self.get_current_url(), self._visible_messages())
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
            # 恢复的是哪个文件，后续就继续写回这个文件（否则恢复后一保存又建新文件）
            self._conv_file_path = file_path
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

    async def start_new_conversation(self, keep_user_task: bool = False):
        """开一个全新的独立对话：清空本地上下文 + 在浏览器里导航到新聊天。

        keep_user_task=True 用于【Agent 自愈】场景（上一轮空回复 / 会话轮数超限）：
        这种情况下网页端换了新会话，但用户的任务没变，如果连用户原话一起清掉，
        落盘的会话 JSON 里就只剩 AI 的话 → 历史标题永远显示「(无标题对话)」，
        回看时也看不到自己当初问了什么。所以要把用户说过的原话带过去。
        用户主动点「新建会话」时传 False，连文件一起换新的。
        """
        _carry = []
        if keep_user_task:
            _carry = [dict(m) for m in self._messages
                      if m.get("role") == "user" and not m.get("internal")]
        self._messages.clear()
        if _carry:
            self._messages.extend(_carry)
        self._session_id = ""
        self._thread_sent_chars = 0
        self._thread_turns = 0
        # 换到一个【全新空白】浏览器会话时，模型确实需要重新看到完整工具列表，
        # 所以重置播种态，下一轮走「完整播种」。
        # （注意：同一 /chat/<id> 里的后续轮次/GUI 后续消息【不会】走这里，
        #    它们靠 bm 上持久的 _native_multi_turn_seeded 走轻量提醒 + 原生多轮记忆。）
        self._proto_seeded = False
        try:
            self._bm._native_multi_turn_seeded = False
        except Exception:
            pass
        if not keep_user_task:
            self._conv_file_path = ""   # 用户主动开新会话 → 下一个新文件
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
                "     for (const n of noise) {"
                "       if (s === n) { isUi = true; break; }"
                "       if (n && s.startsWith(n) && s.length < n.length + 40) { isUi = true; break; }"
                "     }"
                "     if (isUi) continue;"
                "     let stripped = s;"
                "     for (const n of noise) { stripped = stripTok(stripped, n); }"
                "     stripped = stripped.trim();"
                "     if (!stripped) continue;"
                "     kept.push(stripped);"
                "   }"
                "   return kept.join('\\n').trim();"
                " };"
                " const HAN = /[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]/;"
                " const stripTok = (s, n) => {"
                "   if (!n) return s;"
                "   let out = ''; let i = 0;"
                "   while (true) {"
                "     const k = s.indexOf(n, i);"
                "     if (k < 0) { out += s.slice(i); break; }"
                "     const before = k > 0 ? s[k - 1] : '';"
                "     const after = (k + n.length) < s.length ? s[k + n.length] : '';"
                "     const embedded = (before && HAN.test(before)) || (after && HAN.test(after));"
                "     out += s.slice(i, embedded ? (k + n.length) : k);"
                "     i = k + n.length;"
                "   }"
                "   return out;"
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
