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
# 优先级：环境变量 XRZ_DATA_DIR > 自动定位（本文件所在的项目根/xrz_data）> 内置回退。
#
# 【#86 2026-09-24 可迁移关键】旧实现把默认值写死成 D:\软件\XianRenZhangAgent\xrz_data，
# 软件一旦被解压/复制到别的盘符（如 E:\XRZ、C:\tools\XRZ），登录态 / 对话历史 /
# 浏览器 profile / 任务产物就全落在旧 D 盘，"解压即用" 名存实亡。
# 现在默认改为「本文件（agent_core/xrz_paths.py）上溯两级的项目根 / xrz_data」：
# 只要 Python 解释器在软件目录里（PyInstaller onedir 的 _internal、或同目录 pythonw.exe），
# 数据目录就自动跟着软件走，解压到任意位置都能直接跑。
# 仍保留 XRZ_DATA_DIR 环境变量强制覆盖（用户想把数据单独挪到某个盘时）。
def _project_root() -> str:
    """自动定位项目根 = 本文件（agent_core/xrz_paths.py）的上两级目录。

    【#86 冻结模式 2026-09-24】PyInstaller onedir 布局下：
        <软件根>/XianRenZhangAgent.exe        (GUI 壳)
        <软件根>/XianRenZhangBackend.exe      (后端)
        <软件根>/_internal/agent_core/xrz_paths.py
        <软件根>/xrz_data/                    ← 数据目录应放【软件根】，跟着整个文件夹走
    此时本文件上两级是 _internal，再上一级（exe 所在目录）才是「软件根」。
    用 sys.executable 所在目录兜住，保证解压/拷贝到任意位置数据都跟软件走。
    """
    import sys
    base = Path(__file__).resolve().parent.parent
    if getattr(sys, "frozen", False):
        # exe 与 _internal 同级；数据目录放在 exe 所在目录（整个软件文件夹可迁移）
        try:
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir.exists():
                base = exe_dir
        except Exception:
            pass
    return str(base)


def _default_data_root() -> str:
    """项目根/xrz_data；定位失败时回退到旧 D 盘路径（兼容非标准布局）。"""
    try:
        return str(Path(_project_root()) / "xrz_data")
    except Exception:
        return r"D:\软件\XianRenZhangAgent\xrz_data"


DATA_ROOT = Path(os.environ.get("XRZ_DATA_DIR", _default_data_root())).resolve()

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
# 优先使用环境变量，但若路径不存在则回退到中文路径（兼容 D:\软件 和 D:\software）
_FALLBACK_BROWSERS = str(DATA_ROOT / "playwright_browsers")
_PLAYWRIGHT_BROWSERS_FROM_ENV = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
if _PLAYWRIGHT_BROWSERS_FROM_ENV and Path(_PLAYWRIGHT_BROWSERS_FROM_ENV).exists():
    PLAYWRIGHT_BROWSERS_PATH = _PLAYWRIGHT_BROWSERS_FROM_ENV
else:
    PLAYWRIGHT_BROWSERS_PATH = _FALLBACK_BROWSERS

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
