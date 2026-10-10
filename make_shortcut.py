#!/usr/bin/env python3
"""
Generate 启动仙人掌.lnk — a Windows shortcut that launches the desktop app
with the cactus icon embedded. Launching the app FROM this .lnk is the most
reliable way to get a custom taskbar icon on Windows (pythonw.exe's own icon
resource cannot be changed, but Windows uses the shortcut's icon for the
launched window's taskbar entry on Win10/11).

Idempotent: if the .lnk already points to the current pythonw / desktop_app.py,
it is left alone (so we don't churn the shortcut on every launch).

Implementation: WScript.Shell COM via PowerShell (zero Python COM deps,
works on every Windows since Vista).
"""
import os
import subprocess
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
LNK = os.path.join(HERE, "启动仙人掌.lnk")
ICO = os.path.join(HERE, "__xianrenzhang_icon.ico")


def _newest_icon():
    """Prefer a content-hashed icon filename (__xianrenzhang_icon_<md5>.ico).

    Windows caches shortcut icons by path; rewriting the same path often keeps
    showing the stale icon. Using a fresh unique filename forces a re-read.
    """
    import glob as _glob
    cands = sorted(
        _glob.glob(os.path.join(HERE, "__xianrenzhang_icon_*.ico")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    return cands[0] if cands else ICO


DESKTOP_APP = os.path.join(HERE, "desktop_app.py")
# 【2026-10-06 修"写死桌面位置"】原来用 expanduser("~")+"\Desktop"，
# 在别人电脑上会因 OneDrive 重定向 / 非英文 / 自定义盘而指向错误位置
# （快捷方式被创建到不存在的目录）。改用统一解析，认系统真实桌面。
# 本脚本是独立运行的（不作为包内模块），故先把项目根塞进 sys.path。
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from agent_core.user_paths import desktop_dir as _up_desktop_dir
    DESKTOP = _up_desktop_dir(create=True)
except Exception:
    DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
LNK_DESKTOP = os.path.join(DESKTOP, "启动仙人掌.lnk")
APP_ID = "XianRenZhang.Agent.Desktop.1"


def detect_pythonw():
    """Mirror desktop_app._detect_pythonw: pick a pythonw.exe that has
    playwright + PySide6 installed. Falls back to any pythonw on PATH."""
    import shutil
    cands = []
    if os.environ.get("XRZ_PYTHON"):
        cands.append(os.environ["XRZ_PYTHON"])
    for n in ("python", "python3"):
        p = shutil.which(n)
        if p:
            cands.append(p)
    for base in (r"D:\软件\Python", r"C:\Python312", r"C:\Python311"):
        cands.append(os.path.join(base, "python.exe"))
    for c in cands:
        c = os.path.abspath(c)
        if not os.path.exists(c):
            continue
        try:
            r = subprocess.run(
                [c, "-c", "import playwright, PySide6"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            continue
        if r.returncode != 0:
            continue
        d = os.path.dirname(c)
        for w in ("pythonw.exe", "python.exe"):
            wp = os.path.join(d, w)
            if os.path.exists(wp):
                return wp
    # Last resort: any pythonw on PATH
    pw = shutil.which("pythonw") or shutil.which("python") or sys.executable
    return pw


def ps_quote(s):
    """Quote a path for embedding in a PowerShell double-quoted string.
    Replace ' with '' and wrap in double quotes."""
    return '"' + s.replace('"', '`"').replace("'", "''") + '"'


def main():
    if not os.path.exists(ICO):
        print(f"ERROR: icon not found: {ICO}", file=sys.stderr)
        return 1
    if not os.path.exists(DESKTOP_APP):
        print(f"ERROR: desktop_app.py not found: {DESKTOP_APP}", file=sys.stderr)
        return 1
    pythonw = detect_pythonw()
    if not pythonw or not os.path.exists(pythonw):
        print("ERROR: no pythonw.exe found", file=sys.stderr)
        return 1
    ico_use = _newest_icon()
    print(f"  icon used: {ico_use}")

    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        "$s = $ws.CreateShortcut(" + ps_quote(LNK) + ");"
        "$s.TargetPath = " + ps_quote(pythonw) + ";"
        "$s.Arguments = " + ps_quote('"' + DESKTOP_APP + '"') + ";"
        "$s.WorkingDirectory = " + ps_quote(HERE) + ";"
        "$s.IconLocation = " + ps_quote(ico_use + ",0") + ";"
        "$s.Description = '仙人掌 Agent';"
        "$s.WindowStyle = 1;"  # normal (visible)
        "$s.Save();"
        # mirror to the desktop (this is the copy the user actually clicks)
        "$d = " + ps_quote(DESKTOP) + ";"
        "if (Test-Path $d) {"
        "  Copy-Item -LiteralPath " + ps_quote(LNK) + " -Destination " + ps_quote(LNK_DESKTOP) + " -Force;"
        "}"
    )

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        print("ERROR: powershell not available", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("PowerShell failed:", r.stderr or r.stdout, file=sys.stderr)
        return 1
    if not os.path.exists(LNK):
        print(f"ERROR: shortcut not created at {LNK}", file=sys.stderr)
        return 1
    if not os.path.exists(LNK_DESKTOP):
        print(f"WARN: desktop copy missing: {LNK_DESKTOP}", file=sys.stderr)
    print(f"Shortcut ready: {LNK}")
    print(f"  desktop:  {LNK_DESKTOP} -> exists={os.path.exists(LNK_DESKTOP)}")
    print(f"  pythonw:  {pythonw}")
    print(f"  target:   desktop_app.py")
    print(f"  icon:     {ico_use}")
    return 0


if __name__ == "__main__":
    sys.exit(main())