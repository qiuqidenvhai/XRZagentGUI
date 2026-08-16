# -*- coding: utf-8 -*-
"""
仙人掌 Agent —— 统一数据路径。

  硬性约束（用户明确要求）：
  - 所有用户数据（浏览器 profile、cookies、对话历史、任务索引、缓冲、子代理输出、
    Playwright 浏览器二进制）都必须放在 D 盘指定项目目录内，
    **绝不**写入 C 盘用户目录（如 C:/Users/... 或 AppData）。
  - 默认数据根 = 项目目录下的 xrz_data；可通过环境变量 XRZ_DATA_DIR 覆盖。

模块在 import 时即把 PLAYWRIGHT_BROWSERS_PATH 指向 D 盘，保证浏览器二进制也落在 D 盘。
"""

import os
from pathlib import Path

# ---- 数据根目录 ----
# 可被环境变量 XRZ_DATA_DIR 覆盖（例如用户想把数据放到别的 D 盘目录）。
DATA_ROOT = Path(os.environ.get("XRZ_DATA_DIR", r"D:\软件\XianRenZhangAgent\xrz_data")).resolve()

# ---- 仙人掌 Agent 私有目录（对应旧的 ~/.xianrenzhang_agent）----
XRZ_AGENT_DIR = DATA_ROOT / ".xianrenzhang_agent"

# ---- 任务/对话目录（对应旧的 ~/XianRenZhang_tasks）----
TASKS_DIR = DATA_ROOT / "XianRenZhang_tasks"

# ---- 浏览器相关 ----
USER_DATA_DIR = XRZ_AGENT_DIR / "browser_data"          # 旧 deepseek 单 profile 数据
NEW_BROWSER_DATA_ROOT = XRZ_AGENT_DIR / "browser_profiles"
BROWSER_DATA_ROOT = NEW_BROWSER_DATA_ROOT                # platform_browser 用的别名
DEEPSEEK_DATA_DIR = NEW_BROWSER_DATA_ROOT / "deepseek"
COOKIE_FILE = DEEPSEEK_DATA_DIR / "deepseek_cookies.json"
BROWSER_DATA_OLD = XRZ_AGENT_DIR / "browser_data"       # 迁移用（旧路径别名）

# ---- 子代理复用目录（与 browser.py 一致）----
SUBAGENT_USER_DATA_DIR = DEEPSEEK_DATA_DIR
SUBAGENT_COOKIE_FILE = COOKIE_FILE
OLD_USER_DATA_DIR = XRZ_AGENT_DIR / "browser_data"
OLD_COOKIE_FILE = OLD_USER_DATA_DIR / "deepseek_cookies.json"

# ---- 对话历史 / 索引 ----
CONVERSATION_INDEX_PATH = XRZ_AGENT_DIR / "conversation_index.json"
TASKS_INDEX_PATH = XRZ_AGENT_DIR / "tasks.json"
CONVERSATIONS_DIR = TASKS_DIR / "conversations"
GUI_SESSION_DIR = TASKS_DIR / "gui_session"
BUFFERS_DIR = TASKS_DIR / "buffers"
SUBAGENT_TASKS_DIR = XRZ_AGENT_DIR / "tasks"

# ---- 子代理输出 ----
SUBAGENT_OUTPUT_DIR = DATA_ROOT / "subagent_output"

# ---- Playwright 浏览器二进制存放位置（必须在 D 盘）----
PLAYWRIGHT_BROWSERS_PATH = os.environ.get(
    "PLAYWRIGHT_BROWSERS_PATH", str(DATA_ROOT / "playwright_browsers")
)

# 在 import 阶段就把 Playwright 的浏览器目录钉死在 D 盘，
# 这样无论上游是否设置过环境变量，浏览器都不会落到 C:\Users\...\AppData。
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PLAYWRIGHT_BROWSERS_PATH


def ensure_dirs():
    """一次性创建所有需要的目录（幂等）。"""
    for d in (
        XRZ_AGENT_DIR,
        NEW_BROWSER_DATA_ROOT,
        DEEPSEEK_DATA_DIR,
        CONVERSATIONS_DIR,
        GUI_SESSION_DIR,
        BUFFERS_DIR,
        SUBAGENT_TASKS_DIR,
        SUBAGENT_OUTPUT_DIR,
        Path(PLAYWRIGHT_BROWSERS_PATH),
    ):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


# 模块导入时即创建目录
ensure_dirs()


def _copy_missing(src: Path, dst: Path):
    """递归复制：仅复制 dst 中尚不存在的文件，绝不覆盖 D 盘已有数据。"""
    import shutil
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        s = src / item.name
        d = dst / item.name
        if item.is_dir():
            _copy_missing(s, d)
        else:
            if not d.exists():
                try:
                    shutil.copy2(s, d)
                except Exception:
                    pass


def migrate_legacy_c_data():
    """把旧版落在 C 盘用户目录的数据复制到 D 盘 xrz_data（仅复制，不删除 C 盘）。

    目的是：切换到 D 盘后仍能保留已有的登录态（cookies）与对话历史，
    之后所有新数据也都只落在 D 盘，不再触碰 C 盘。
    """
    import shutil
    legacy_root = Path.home()
    pairs = [
        (legacy_root / ".xianrenzhang_agent", XRZ_AGENT_DIR),
        (legacy_root / "XianRenZhang_tasks", TASKS_DIR),
    ]
    for src, dst in pairs:
        if not src.exists():
            continue
        try:
            _copy_missing(src, dst)
            print(f"[迁移] 已从 C 盘复制旧数据: {src} -> {dst}")
        except Exception as e:
            print(f"[迁移] 复制 {src} 失败（可忽略）: {e}")


# 应用启动时调用（见 terminal.py main）
def maybe_migrate():
    try:
        migrate_legacy_c_data()
    except Exception:
        pass
