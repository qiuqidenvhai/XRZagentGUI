# -*- coding: utf-8 -*-
"""
仙人掌 Agent —— 原生启动器（编译成 XianRenZhangAgent.exe，PyInstaller onefile）。

用户诉求（原话）：「他不应该是 exe 吗，为什么是 .bat，那是源代码的启动方式，
不是软件的启动方式。」

设计（最小风险、可迁移）：
  这个 exe 本身【不做任何重活】（没有 Qt / Chromium / pyc 后端，保证 100% 稳定冻结）。
  它只是「软件本体」的入口：双击 → 解析自己所在目录 → 设好可迁移环境变量 →
  拉起【已实测通过】的 CPython 侧车（runtime\pythonw.exe）跑 GUI 壳 desktop_app.py，
  GUI 壳再自动拉起后端（runtime\pythonw.exe terminal.py）。

  这样：
    · 用户看到的是「一个仙人掌 exe 软件」，双击即用，不是 .bat / 跑源码。
    · 所有后端/GUI 逻辑仍走「未冻结的」侧车解释器，terminal.pyc + 5 个热补丁 +
      大量 __file__ 相对资源定位全部原样工作（与已验证的 .bat 路径完全一致，零回归风险）。
    · 整个文件夹拷到任意盘符 / 任意电脑，双击 XianRenZhangAgent.exe 即可（免装 Python）。

  冻结模式（sys.frozen）：Path(sys.executable) = 该 exe 在磁盘上的真实位置，
  其所在目录即「软件根」。非冻结时（开发机直接 python launcher.py）回退到本文件目录。
"""

import os
import sys
import time


def _root_dir() -> str:
    """软件根目录 = exe 所在目录（冻结）或本文件目录（源码）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _write_console_line(msg: str):
    """尽量把关键信息打到控制台（有控制台就打印，没有就静默）。"""
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _find_pythonw(root: str) -> str:
    """定位便携解释器（无黑窗）：runtime\pythonw.exe 优先，回退 runtime\python.exe。"""
    for cand in (
        os.path.join(root, "runtime", "pythonw.exe"),
        os.path.join(root, "runtime", "python.exe"),
    ):
        if os.path.exists(cand):
            return cand
    # 最后回退：系统 pythonw（目标机已装 Python 时可用；未装则启动失败）
    for cand in ("pythonw", "python"):
        try:
            import shutil as _sh
            p = _sh.which(cand)
            if p:
                return p
        except Exception:
            pass
    return "pythonw"


def _self_test(root: str, pyw: str) -> int:
    """XRZ_LAUNCH_SELFTEST=1：只做路径/可执行性自检，不真开 GUI。

    逐条打印检查结果并以非零码退出失败项，供打包验收用（headless，不抢焦点）。
    """
    import json as _json
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        _write_console_line(
            "  [%s] %s%s" % ("OK" if ok else "FAIL", name, ("  " + detail) if detail and not ok else ""))

    gui = os.path.join(root, "desktop_app.py")
    term = os.path.join(root, "terminal.py")
    gui_html = os.path.join(root, "gui.html")
    browser_dir = os.path.join(root, "xrz_data", "playwright_browsers")

    check("软件根目录存在", os.path.isdir(root), root)
    check("GUI 入口 desktop_app.py", os.path.isfile(gui), gui)
    check("后端入口 terminal.py", os.path.isfile(term), term)
    check("后端逻辑 terminal.pyc", os.path.isfile(os.path.join(root, "terminal.pyc")))
    check("gui.html", os.path.isfile(gui_html), gui_html)
    check("agent_core/", os.path.isdir(os.path.join(root, "agent_core")))
    check("便携解释器 runtime/pythonw", os.path.isfile(pyw), pyw)
    check("Playwright 浏览器目录", os.path.isdir(browser_dir), browser_dir)

    # 验证解释器确实能 import 关键依赖
    probe = os.path.join(pyw, "-c") if False else None
    try:
        import subprocess as _sp
        r = _sp.run([pyw, "-c", "import playwright, PySide6; print('runtime-deps-ok')"],
                    capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        detail = (r.stderr or r.stdout or "").strip()[:200]
    except Exception as e:
        ok, detail = False, str(e)
    check("runtime 依赖可导入(playwright+PySide6)", ok, detail if not ok else "runtime-deps-ok")

    all_ok = all(c["ok"] for c in checks)
    _write_console_line("\n[selftest] 结果: %d/%d 通过" % (sum(1 for c in checks if c["ok"]), len(checks)))
    try:
        with open(os.path.join(root, "_launcher_selftest.json"), "w", encoding="utf-8") as f:
            _json.dump({"root": root, "pythonw": pyw, "all_ok": all_ok, "checks": checks},
                       f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    _write_console_line("[selftest] 报告已写: _launcher_selftest.json")
    return 0 if all_ok else 1


def main() -> int:
    root = _root_dir()
    pyw = _find_pythonw(root)

    if os.environ.get("XRZ_LAUNCH_SELFTEST") == "1":
        return _self_test(root, pyw)

    gui = os.path.join(root, "desktop_app.py")
    if not os.path.exists(gui):
        _write_console_line("[launcher] 找不到 GUI 入口: %s" % gui)
        # 给桌面用户一个可读提示（不闪退）
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "仙人掌 Agent 启动失败：\n找不到 GUI 入口 desktop_app.py。\n请确认整个软件文件夹完整。",
                "仙人掌 Agent", 0x10)
        except Exception:
            pass
        return 1

    env = os.environ.copy()
    env["XRZ_DATA_DIR"] = os.path.join(root, "xrz_data")
    bdir = os.path.join(root, "xrz_data", "playwright_browsers")
    if os.path.isdir(bdir):
        env["PLAYWRIGHT_BROWSERS_PATH"] = bdir
    env["XRZ_NO_GUI"] = "1"
    env.setdefault("no_proxy", "127.0.0.1,localhost")
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")

    # 拉起 GUI 壳（它内部会再拉起后端）。CREATE_NEW_PROCESS_GROUP + DETACHED，
    # 让 exe 启动器本身可以退出，GUI/后端独立存活（关窗口 agent 继续跑，与原设计一致）。
    import subprocess
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [pyw, gui],
        cwd=root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    _write_console_line("[launcher] 已启动 GUI: %s  (解释器: %s)" % (gui, pyw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
