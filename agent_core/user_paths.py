# -*- coding: utf-8 -*-
"""桌面 / 用户目录路径解析（跨机器、跨账号、跨 OneDrive 重定向）。

【为什么必须有这个模块】
旧代码把 `C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop` 写死在系统提示词示例和若干
脚本里。这是给别人用的软件，写死用户名必然在别人电脑上出错：
  1. 用户名不同      → 别人是 C:\\Users\\zhangsan\\Desktop
  2. 桌面被 OneDrive 重定向 → C:\\Users\\zhangsan\\OneDrive\\Desktop
  3. 桌面被挪到自定义盘 → D:\\我的文档\\Desktop
  4. 系统语言/非英文  → 桌面文件夹不一定叫 "Desktop"
  5. 域账号 / 无人登录态 → os.path.expanduser("~") 可能拿到 C:\\Users\\Default
所以【永远不要】拼接固定用户名路径，必须调用本模块动态解析。

解析优先级（全部纯 Python，不调用任何 ctypes Win32 API —— 那些调用在部分
环境（受限沙箱 / 杀软 / 非 Windows 容器）会直接 SIGTERM 掉整个进程，
无法用 try/except 捕获，会把主程序连带打死）：
  1. 注册表 HKCU\\...\\User Shell Folders\\Desktop（expandvars 展开）
     —— 系统权威值，天然处理 OneDrive 重定向 / 非英文 / 自定义盘
  2. 环境变量 XRZ_DESKTOP（用户/运维可显式覆盖，最高优先级逃生口）
  3. %USERPROFILE%\\Desktop
  4. %HOME%\\Desktop
  5. expanduser("~")\\Desktop
  6. profile 根目录（桌面目录被删时兜底）
"""

from __future__ import annotations

import os
import sys

# 注册表里 Desktop 可能不是物理目录（OneDrive 未落盘时是纯云占位）。
# 这种情况下 os.path.isdir 仍为 True（占位符目录），可正常写入。
_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
_REG_VAL = "Desktop"


def _from_registry() -> str | None:
    """从 HKCU\\User Shell Folders 读 Desktop。系统权威值。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _REG_VAL)
        if val:
            return os.path.expandvars(str(val)).strip()
    except OSError:
        pass
    return None


def _from_env() -> str | None:
    """环境变量显式覆盖（用户可自己指定桌面位置）。"""
    v = os.environ.get("XRZ_DESKTOP", "").strip()
    return v if v else None


def _profile_dir() -> str:
    """当前用户 profile 根目录。域账号/无人登录时 expanduser 可能不可靠。"""
    for env in ("USERPROFILE", "HOME"):
        v = os.environ.get(env)
        if v and os.path.isdir(v):
            return v
    try:
        home = os.path.expanduser("~")
        if home and os.path.isdir(home):
            return home
    except Exception:
        pass
    return os.path.abspath(os.getcwd())


def user_profile() -> str:
    """当前用户 profile 根目录。"""
    return _profile_dir()


def desktop_dir(create: bool = False) -> str:
    """返回当前机器【真实的】桌面绝对路径。

    create=True 时目录不存在则创建。
    绝不写死任何用户名 —— 这是给别人用的软件。
    """
    prof = _profile_dir()
    # 1) 显式覆盖：用户自己指定的，【无条件尊重】——哪怕目录当前不存在
    #    （网络盘未挂载 / OneDrive 未同步 / 首次运行尚未创建）。
    #    这种情况若因 isdir 为 False 而回退，就等于无视用户配置，比回退更糟。
    _env = _from_env()
    if _env:
        return os.path.abspath(os.path.expandvars(_env))

    # 2) 注册表（系统权威值，天然处理 OneDrive 重定向 / 非英文 / 自定义盘）
    #    3) %USERPROFILE%\Desktop
    #    4) %HOME%\Desktop
    #    5) ~\Desktop —— 以上都要求目录真实存在
    cands = [
        _from_registry(),
        os.path.join(prof, "Desktop"),
    ]
    up_home = os.environ.get("HOME")
    if up_home:
        cands.append(os.path.join(up_home, "Desktop"))
    cands.append(os.path.join(os.path.expanduser("~"), "Desktop"))

    for c in cands:
        if not c:
            continue
        p = os.path.abspath(os.path.expandvars(c))
        if os.path.isdir(p):
            return p

    # 全都不存在（干净系统 / 桌面目录被删）→ 用 profile 根
    p = prof
    if create:
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            pass
    return p


def desktop_with_sep() -> str:
    """桌面路径，保证以分隔符结尾（方便直接字符串拼接子路径）。"""
    d = desktop_dir()
    return d if d.endswith(("\\", "/")) else d + os.sep


def escape_for_prompt(path: str) -> str:
    """把路径转成放进 f-string 提示词里的安全字面量（反斜杠双写）。"""
    return path.replace("\\", "\\\\")


def json_path(path: str) -> str:
    """转成 JSON 字符串值形式（含双引号，内部反斜杠/引号已转义）。"""
    import json

    return json.dumps(path, ensure_ascii=False)


if __name__ == "__main__":
    d = desktop_dir()
    print("desktop_dir   :", d)
    print("user_profile  :", user_profile())
    print("exists        :", os.path.isdir(d))
    print("with_sep      :", desktop_with_sep())
    print("prompt_escape :", escape_for_prompt(d))
    print("json_path     :", json_path(d))
