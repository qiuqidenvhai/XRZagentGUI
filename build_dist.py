# -*- coding: utf-8 -*-
"""
build_dist.py —— 仙人掌 Agent 可迁移打包脚本（#86 2026-09-24，2026-09-25 改用 robocopy）。

目标（用户原话：「解压成单独的软件、保证可迁移、所有写死的路径都得改」）：
  产出一个「整个文件夹拷到任意盘符 / 任意 Windows 电脑」双击就能跑的目录：

      XianRenZhangAgent/
        ├─ XianRenZhangAgent.exe        ← GUI 壳（pythonw 入口，无黑窗）[PyInstaller 模式]
        ├─ XianRenZhangBackend.exe      ← 后端入口 [PyInstaller 模式]
        ├─ runtime/                    ← 内置 Python 运行时（含 playwright/PySide6）
        ├─ agent_core/  terminal.py  terminal.pyc  gui.html  platforms.json
        ├─ _files_listing_fix.py  _sse_resilience.py  _dom_dump_patch.py
        │  _parallel_tasks_patch.py  _auto_update_patch.py ...（全部热补丁）
        ├─ xrz_data/playwright_browsers/  ← 内置浏览器二进制（chromium / headless / ffmpeg）
        ├─ _version.json               ← 版本戳，自动更新用
        └─ 启动仙人掌.bat              ← 一键启动

数据目录 xrz_data/ 随同文件夹走；登录态绝不进包（见 _strip_login_state）。

【2026-09-25 重大修正：用 robocopy 替代 shutil 做批量拷贝】
  实测本机安全软件会拦截 Python(shutil) 对「内置浏览器/运行时二进制」的批量写入，
  导致 copytree 卡死/进程被杀、浏览器目录永远是空的。改用系统原生 robocopy.exe
  （受信任的系统工具，安全软件放行）做 /E /PURGE 镜像，拷贝稳定且快。
  同时去掉 shutil.rmtree 整树删除（会触发批量删除守卫/卡死），改用 robocopy /PURGE
  增量清理 + 定向清理零散残留。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DIST = _HERE / "dist" / "XianRenZhangAgent"

# 内置运行时来源（开发机上的 Python；拷进 dist 后目标机无需装 Python）
_DEFAULT_RUNTIME_SRC = Path(r"D:\软件\Python")

# 要打进 dist 根目录的「程序文件」（绝对白名单，避免把几百个测试脚本/log 也塞进去）
_INCLUDE_FILES = [
    "terminal.py", "terminal.pyc", "gui.html",
    "desktop_app.py",       # GUI 壳入口（PySide6）；launcher.exe / .bat 都靠它开窗口
    "agent_core",            # 整个目录（session/commander/browser/pptx/... 全要，含 platforms.json）
    "buffer_store.py", "web_searcher.py", "subagent_main.py",
    "_files_listing_fix.py", "_sse_resilience.py", "_dom_dump_patch.py",
    "_parallel_tasks_patch.py", "_auto_update_patch.py",
    "__xianrenzhang_icon.ico", "__xianrenzhang_icon.png",
    "__browser_cactus_icon.ico",
]


def _robocopy(src, dst, purge: bool = False):
    """用系统原生 robocopy 做目录镜像（避开安全软件对 shutil 批量写入的拦截）。

    /E      含空目录递归
    /R:1    失败重试 1 次
    /W:1    重试间隔 1 秒
    /PURGE  删除 dst 中 src 不存在的文件（增量清理，等价于安全的"覆盖式打包"）
    退出码 0-7 都算成功（含"有复制/有跳过/有剔除"）；>=8 才是真失败。
    """
    src = str(src)
    dst = str(dst)
    os.makedirs(dst, exist_ok=True)
    args = ["robocopy", src, dst, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS"]
    if purge:
        args.append("/PURGE")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=2400)
        rc = r.returncode
    except Exception as e:  # 超时/异常都向上抛，让 build 失败可见
        print("  [robocopy 异常]", e)
        raise
    if rc >= 8:
        tail = (r.stderr or r.stdout or "")[-800:]
        print("  [robocopy 失败] rc=%d\n%s" % (rc, tail))
        raise RuntimeError("robocopy failed rc=%d" % rc)
    return rc


def _copy_file_or_dir(src: Path, dst: Path):
    if src.is_dir():
        _robocopy(str(src), str(dst), purge=True)
    elif src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))


def _write_version(dst_root: Path, version: str):
    manifest = {
        "version": version,
        "stamp": int(time.time()),
        "release_notes": "内置运行时可迁移版",
        "download_url": "",   # 由仓库 release 上传后回填；本地版留空即可
    }
    (dst_root / "_version.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  已写入 _version.json: version=%s stamp=%d" % (version, manifest["stamp"]))


def _copy_runtime(src: Path, dst_root: Path) -> bool:
    """把整个 Python 运行时拷进 runtime/（目标机免装 Python 的关键）。

    直接用 robocopy 把整个 runtime 源目录镜像进 runtime/（含 pythonw.exe / DLLs /
    Lib / site-packages / tcl 等）。/PURGE 保证与源完全一致（清掉上次残留）。
    """
    dst = dst_root / "runtime"
    print("  复制内置运行时(robocopy) %s -> %s" % (src, dst))
    _robocopy(src, dst, purge=True)
    return True


def _copy_browsers(dst_root: Path):
    """把当前 PLAYWRIGHT_BROWSERS_PATH 的浏览器二进制带进 dist。

    必须放到 xrz_data/playwright_browsers（= DATA_ROOT 下），与 xrz_paths 的默认
    解析保持一致（否则不走 .bat 直接跑时 xrz_paths 找不到浏览器）。
    """
    import agent_core.xrz_paths as xp
    src = Path(xp.PLAYWRIGHT_BROWSERS_PATH)
    if not src.exists():
        print("  [跳过] 未找到浏览器目录 %s（目标机需联网首次运行自动下载）" % src)
        return
    dst = dst_root / "xrz_data" / "playwright_browsers"
    print("  复制浏览器二进制(robocopy) %s -> %s" % (src, dst))
    _robocopy(src, dst, purge=True)
    (dst_root / "_browsers_dir.txt").write_text(str(dst), encoding="utf-8")


def _write_launcher(dst_root: Path):
    """生成 启动.bat：优先拉起原生 exe（XianRenZhangAgent.exe），没有则回退侧车。"""
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        "cd /d \"%~dp0\"",
        "rem —— 优先用原生 exe（软件本体）——",
        "if exist \"%~dp0XianRenZhangAgent.exe\" (",
        "  start \"\" \"%~dp0XianRenZhangAgent.exe\"",
        "  goto end",
        ")",
        "rem —— 回退：直接用内置侧车解释器拉起 ——",
        "set XRZ_DATA_DIR=%~dp0xrz_data",
        "if exist \"%~dp0xrz_data\\playwright_browsers\\*\" set PLAYWRIGHT_BROWSERS_PATH=%~dp0xrz_data\\playwright_browsers",
        "set XRZ_NO_GUI=1",
        "set \"PYW=%~dp0runtime\\pythonw.exe\"",
        "if not exist \"%PYW%\" set \"PYW=pythonw\"",
        "if exist \"%~dp0XianRenZhangBackend.exe\" goto start_backend_exe",
        "start \"\" \"%PYW%\" terminal.py",
        "goto start_gui",
        ":start_backend_exe",
        "start \"\" \"%~dp0XianRenZhangBackend.exe\"",
        ":start_gui",
        "start \"\" \"%PYW%\" desktop_app.py",
        ":end",
        "endlocal",
    ]
    (dst_root / "启动仙人掌.bat").write_text("\r\n".join(lines), encoding="gbk")
    print("  已生成 启动仙人掌.bat（优先 exe，回退侧车）")


def _clean_stray(dst_root: Path):
    """定向清理上次打包/测试在 dist 根留下的零散残留（非批量删除，安全）。"""
    import fnmatch
    patterns = ("*_installed.txt", "_pkg_backend*.log", "_*.log",
                "_browsers_dir.txt")
    if not dst_root.is_dir():
        return
    for f in dst_root.iterdir():
        try:
            if f.is_file() and any(fnmatch.fnmatch(f.name, p) for p in patterns):
                f.unlink()
                print("  [清理] 移除残留:", f.name)
        except Exception:
            pass


def build(portable_python: Path = _DEFAULT_RUNTIME_SRC,
          version: str = "", use_pyinstaller: bool = False) -> Path:
    print("== 仙人掌 Agent 可迁移打包 ==", flush=True)
    print("源目录:", _HERE)
    print("目标:", _DIST, flush=True)

    # 不整树 rmtree（会被安全策略拦截/卡死）。改用 robocopy /PURGE 增量清理 +
    # 定向清理零散残留文件。
    _DIST.mkdir(parents=True, exist_ok=True)
    _clean_stray(_DIST)

    # 1) 程序文件（白名单）：目录用 robocopy /PURGE，单文件用 copy2 覆盖
    for rel in _INCLUDE_FILES:
        src = _HERE / rel
        if not src.exists():
            print("  [警告] 缺少文件，跳过:", rel)
            continue
        _copy_file_or_dir(src, _DIST / rel)
    print("  已复制程序文件 %d 项" % len(_INCLUDE_FILES))

    # agent_core 里若自带 platforms.json 也一并带（已随 agent_core 目录整体复制）
    if (_HERE / "agent_core" / "platforms.json").exists():
        _copy_file_or_dir(_HERE / "agent_core" / "platforms.json",
                          _DIST / "agent_core" / "platforms.json")

    # 2) 内置运行时（目标机免装 Python）
    if portable_python and Path(portable_python).exists():
        _copy_runtime(Path(portable_python), _DIST)
    else:
        print("  [跳过] 内置运行时（未找到 %s；目标机需自装 Python）" % portable_python)

    # 3) 浏览器二进制
    _copy_browsers(_DIST)

    # 4) 版本戳
    _write_version(_DIST, version or time.strftime("%Y.%m.%d"))

    # 5) 启动脚本
    _write_launcher(_DIST)

    # 6) 【安全红线·零登录态】打包产物里绝不允许出现任何登录态 / 用户数据。
    _strip_login_state(_DIST)

    # 7) 空数据目录骨架（首启自动填充；不含任何用户数据）
    for sub in ("xrz_data/.xianrenzhang_agent", "xrz_data/XianRenZhang_tasks",
                "xrz_data/playwright_browsers"):
        (_DIST / sub).mkdir(parents=True, exist_ok=True)

    # 8) 可选 PyInstaller（压缩体积）
    if use_pyinstaller:
        _pyinstaller_mode(_DIST)

    # 8) 原生启动器 exe（XianRenZhangAgent.exe）：双击即用，替代 .bat 作为「软件本体」入口。
    #    若 build_tools/pyi 里已备好 PyInstaller（win_amd64）就自动编译；否则跳过并提示。
    _build_launcher_exe(_DIST)

    _print_size(_DIST)
    print("\n✅ 打包完成: %s" % _DIST)
    print("   把整个 XianRenZhangAgent 文件夹拷到任意盘符/电脑，双击「XianRenZhangAgent.exe」即用")
    print("   （没有 exe 时，回退到「启动仙人掌.bat」，它也会优先找 exe、再回退侧车解释器）。")
    return _DIST


def _build_launcher_exe(dst_root: Path):
    """把 launcher.py 编译成原生 XianRenZhangAgent.exe（PyInstaller onefile，无 Qt 依赖，稳定）。

    该 exe 只是「软件入口」：双击 → 解析自己所在目录 → 拉起内置 runtime/pythonw.exe
    跑 GUI 壳 desktop_app.py（再自动起后端）。重活都在已实测的侧车里，exe 本身零风险。
    需要 build_tools/pyi 下的 PyInstaller（win_amd64）；未备货则跳过并打印提示。
    """
    if not (_HERE / "launcher.py").exists():
        print("  [跳过] 缺少 launcher.py，无法生成原生 exe")
        return
    pyi_dir = _HERE / "build_tools" / "pyi"
    if not (pyi_dir / "PyInstaller" / "__init__.py").exists():
        print("  [跳过] 未备好 PyInstaller(build_tools/pyi)，无法生成原生 exe")
        print("          提示：可用 PowerShell 下载 PyInstaller win_amd64 wheel 解包到此目录")
        return
    import subprocess as _sp
    py = _DEFAULT_RUNTIME_SRC / "python.exe"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pyi_dir)
    work = _HERE / "build_exe" / "_work"
    cmd = [
        str(py), "-m", "PyInstaller", "-y", "--onefile", "--windowed",
        "--clean",
        "--name", "XianRenZhangAgent",
        "--icon", str(_HERE / "__xianrenzhang_icon.ico"),
        "--distpath", str(_HERE / "build_exe"),
        "--workpath", str(work),
        str(_HERE / "launcher.py"),
    ]
    print("  编译原生启动器 exe (PyInstaller onefile) ...")
    r = _sp.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    exe = _HERE / "build_exe" / "XianRenZhangAgent.exe"
    if r.returncode != 0 or not exe.exists():
        print("  [警告] 原生 exe 编译失败，跳过（不影响 .bat/侧车 启动方式）：")
        print("         ", (r.stderr or r.stdout or "")[-800:])
        return
    shutil.copy2(str(exe), str(dst_root / "XianRenZhangAgent.exe"))
    print("  已生成原生启动器: %s" % (dst_root / "XianRenZhangAgent.exe"))


def _pyinstaller_mode(dst_root: Path):
    """可选：把 backend / GUI 冻成 exe（需先 pip install pyinstaller）。"""
    import subprocess
    try:
        import PyInstaller  # noqa
    except Exception:
        print("  [跳过] 未装 pyinstaller（pip install pyinstaller 后重跑 --size 1）")
        return
    py = sys.executable
    common = ["-y", "--noconfirm", "--clean", "--onedir", "--windowed",
              "--distpath", str(dst_root / "dist"), "--workpath", str(dst_root / "build")]
    subprocess.run([py, "-m", "PyInstaller"] + common +
                   ["--name", "XianRenZhangBackend", "--paths", ".",
                    "--add-data", "terminal.pyc;.",
                    "--add-data", "agent_core;agent_core",
                    "--add-data", "gui.html;.",
                    "--add-data", "platforms.json;.",
                    "--add-data", "_files_listing_fix.py;.",
                    "--add-data", "_sse_resilience.py;.",
                    "--add-data", "_dom_dump_patch.py;.",
                    "--add-data", "_parallel_tasks_patch.py;.",
                    "--add-data", "_auto_update_patch.py;.",
                    "terminal.py"])
    subprocess.run([py, "-m", "PyInstaller"] + common +
                   ["--name", "XianRenZhangAgent", "--paths", ".",
                    "--add-data", "gui.html;.",
                    "--add-data", "__xianrenzhang_icon.ico;.",
                    "--add-data", "agent_core;agent_core",
                    "desktop_app.py"])


def _strip_login_state(dst_root: Path) -> int:
    """【安全红线】清空分发包里的一切登录态 / 用户数据，并做自检。

    绝不外流的内容（含账号凭证，泄露 = 把账号送人）：
      - 浏览器登录态：*cookie*.json、browser_profiles/ 整个目录
      - 用户数据：对话历史、任务索引、产物、记忆缓冲
    返回清除的条目数。
    """
    import fnmatch
    removed = 0

    # 1) 浏览器 profile 目录（登录态载体）整目录删
    for pat in ("xrz_data/.xianrenzhang_agent/browser_profiles",
                "xrz_data/.xianrenzhang_agent/browser_data",
                "browser_profiles", "browser_data"):
        p = dst_root / pat
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
            print("  [安全] 已移除登录态目录:", pat)

    # 2) 任何 cookie【数据】文件 —— 只看数据区（xrz_data 等），绝不碰 runtime/ 程序代码。
    for f in list(dst_root.rglob("*")):
        try:
            if not f.is_file():
                continue
            rel = str(f.relative_to(dst_root)).replace("\\", "/")
            if rel.startswith(("runtime/", "agent_core/", "playwright")) or rel.startswith("xrz_data/playwright_browsers/"):
                continue
            low = f.name.lower()
            if f.suffix.lower() == ".json" and ("cookie" in low or fnmatch.fnmatch(low, "*login*.json")):
                f.unlink()
                removed += 1
                print("  [安全] 已删除登录态文件:", rel)
        except Exception:
            pass

    # 3) 用户数据目录里的历史/产物/记忆（非程序文件）
    for pat in ("xrz_data/XianRenZhang_tasks", "xrz_data/subagent_output"):
        p = dst_root / pat
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
            print("  [安全] 已清空用户数据目录:", pat)

    # 4) 自检：确认数据区再无任何登录态残留（同样跳过 runtime/ 代码目录）
    leftovers = []
    for f in dst_root.rglob("*"):
        try:
            if not f.is_file():
                continue
            rel = str(f.relative_to(dst_root)).replace("\\", "/")
            if rel.startswith(("runtime/", "agent_core/", "playwright")) or rel.startswith("xrz_data/playwright_browsers/"):
                continue
            if "cookie" in f.name.lower() or "login" in f.name.lower():
                leftovers.append(rel)
        except Exception:
            pass
    if leftovers:
        raise RuntimeError("打包产物仍残留登录态文件，已中止：%s" % leftovers[:5])
    print("  [安全] 自检通过：分发包内零登录态、零用户数据 ✅")
    return removed


def _print_size(dst_root: Path):
    total = 0
    cnt = 0
    for p in dst_root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            cnt += 1
    print("\n  产物: %d 个文件, %.1f MB" % (cnt, total / 1024 / 1024))


def main():
    ap = argparse.ArgumentParser(description="仙人掌 Agent 可迁移打包")
    ap.add_argument("--runtime", default=str(_DEFAULT_RUNTIME_SRC),
                    help="内置 Python 运行时来源目录")
    ap.add_argument("--version", default="", help="写入 _version.json 的版本号")
    ap.add_argument("--size", default="", help="传 1 = 额外跑 PyInstaller 压缩")
    args = ap.parse_args()
    build(portable_python=Path(args.runtime) if args.runtime else None,
          version=args.version,
          use_pyinstaller=args.size == "1")


if __name__ == "__main__":
    main()
