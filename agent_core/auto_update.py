# -*- coding: utf-8 -*-
"""
agent_core/auto_update.py —— 仙人掌 Agent 自动更新（#87 2026-09-24）。

设计原则（用户明确要求：自动更新，但绝不动用户数据）：
  1) 版本来源 = 本地 manifest（_version.json，打包脚本生成）。
     仓库里同时推一个 update_manifest.json（含下载直链 + 本地版本戳），
     更新器比对「本地版本戳 < 远端版本戳」决定是否提示。
  2) 更新对象 = 程序文件（exe / agent_core / 热补丁 / gui.html / platforms.json），
     永远只替换「安装目录」，绝不碰 xrz_data（登录态 / 历史 / 浏览器 profile）。
     下载下来的压缩包内不含 xrz_data，解压覆盖前会做「白名单文件」过滤。
  3) 执行方式：
     - PyInstaller 模式：把新版 zip 解压到 <安装目录>_update_tmp/，
       生成交接脚本（先拷回、停旧进程、换新文件、重启），替换后自动拉起新版。
     - 源码模式（pythonw.exe 直接跑 terminal.py 的开发环境）：同样走「临时目录 + 复制」，
       绝不直接改正在运行的 .py（Windows 下运行中文件可被覆盖但行为不确定，复制最稳）。
  4) 断网 / 下载失败 / 版本相同一律静默返回 False，不打断正常使用。

manifest 文件格式（放在软件根目录，打包脚本生成）：
  {
    "version": "2026.09.24",
    "stamp": 1758700000,           # 远端比对用时间戳
    "download_url": "https://github.com/qiuqidenvhai/XRZagentGUI/releases/download/.../XianRenZhangAgent_xxx.zip",
    "release_notes": "修复 PPT 模板、暂停键…"
  }
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

_GH_REPO = "qiuqidenvhai/XRZagentGUI"
_GH_API = "https://api.github.com/repos/%s/releases/latest" % _GH_REPO

# 白名单：只允许这些顶层条目被「新版」覆盖旧安装目录（防 zip 里混进危险文件）
_ALLOWED_TOP = {
    "agent_core", "gui.html", "terminal.py", "terminal.pyc",
    "_files_listing_fix.py", "_sse_resilience.py", "_dom_dump_patch.py",
    "_parallel_tasks_patch.py", "platforms.json",
    "desktop_app.py", "desktop_app_main.py", "auto_update.py",
    "buffer_store.py", "web_searcher.py", "requirements.txt",
    "_version.json", "XianRenZhangAgent.exe",
}

# 永远绝不覆盖（用户数据 & 日志），即使压缩包里带同名文件
_NEVER_OVERWRITE_GLOBS = (
    "xrz_data/*", "browser_profiles/*", "*.log", "_*.out", "_*.png",
    "logs/*", "playwright_browsers/*",
)


def _install_root() -> Path:
    """定位「安装目录」（程序文件所在根），与数据目录 xrz_data 严格分离。"""
    if getattr(sys, "frozen", False):
        # PyInstaller：sys._MEIPASS = 程序文件根（onedir 时 = _internal 所在的那层）
        p = getattr(sys, "_MEIPASS", None)
        if p and Path(p).exists():
            return Path(p)
        return Path(sys.executable).parent
    # 源码模式：本文件在 agent_core/ 下，上溯一级
    return Path(__file__).resolve().parent.parent


def _version_file(root: Path) -> Path:
    return root / "_version.json"


def _read_local_stamp(root: Path):
    try:
        m = json.loads(_version_file(root).read_text(encoding="utf-8"))
        return int(m.get("stamp") or 0), str(m.get("version") or "")
    except Exception:
        return 0, ""


def _download(url: str, dest: Path, timeout: int = 180) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "XianRenZhangAgent-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(str(dest), "wb") as f:
            shutil.copyfileobj(r, f)
        return dest.exists() and dest.stat().st_size > 1024
    except Exception:
        return False


def check_update(root: Path = None) -> dict:
    """查 GitHub 最新 release 的下载直链 + 比对本地版本戳。

    返回 {"has_update": bool, "stamp": int, "version": str, "url": str,
          "notes": str}。任何异常 → has_update=False（静默降级）。
    """
    root = Path(root or _install_root())
    local_stamp, local_ver = _read_local_stamp(root)
    try:
        req = urllib.request.Request(_GH_API, headers={
            "User-Agent": "XianRenZhangAgent-Updater/1.0",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            rel = json.loads(r.read().decode("utf-8") or "{}")
        # 找 .zip 资产（Windows 用户）
        asset = next((a for a in rel.get("assets", [])
                      if str(a.get("name", "")).lower().endswith(".zip")), None)
        if not asset:
            return {"has_update": False, "reason": "no_zip_asset"}
        remote_stamp = 0
        m = re.search(r"(\d{9,11})", str(asset.get("name", "")))
        if m:
            remote_stamp = int(m.group(1))
        if not remote_stamp:
            remote_stamp = int(time.time())
        return {
            "has_update": remote_stamp > local_stamp,
            "remote_stamp": remote_stamp,
            "local_stamp": local_stamp,
            "version": str(rel.get("tag_name") or asset.get("name", "")),
            "url": asset.get("browser_download_url", ""),
            "notes": str(rel.get("body") or "")[:500],
        }
    except Exception:
        return {"has_update": False, "reason": "check_failed"}


def _safe_members(zipf: zipfile.ZipFile) -> list:
    """过滤白名单：只保留可覆盖程序文件的成员，丢弃数据/日志。"""
    import fnmatch
    keep = []
    for info in zipf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir():
            continue
        top = name.split("/", 1)[0]
        if top not in _ALLOWED_TOP:
            continue
        # 再挡一道：任何落在数据/日志/浏览器目录里的条目，不管顶层叫什么名都不许动
        if any(fnmatch.fnmatch(name, pat) for pat in _NEVER_OVERWRITE_GLOBS):
            continue
        if fnmatch.fnmatch(name, "xrz_data*") or fnmatch.fnmatch(name, "*.log"):
            continue
        keep.append(name)
    return keep


def apply_update(zip_path, root: Path = None) -> tuple:
    """把新版 zip 覆盖安装到安装目录（白名单过滤，绝不动 xrz_data）。

    返回 (ok: bool, message: str)。
    """
    root = Path(root or _install_root())
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return False, "更新包不存在: %s" % zip_path
    try:
        z = zipfile.ZipFile(str(zip_path))
        keep = _safe_members(z)
        if not keep:
            return False, "更新包里没有任何可覆盖的程序文件（可能下错包）"
        # 逐文件解压到安装目录（同名覆盖、目录自动创建）
        n = 0
        for name in keep:
            dst = root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(str(dst), "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
        z.close()
    except Exception as e:
        return False, "解压覆盖失败: %s" % e

    # 更新本地版本戳（下次启动时 check_update 就不会再提示同一版）
    try:
        info = _read_local_stamp(root)
        stamp = info[0]
        manifest = {
            "version": "updated_%d" % int(time.time()),
            "stamp": int(time.time()),
            "note": "自更新完成",
        }
        _version_file(root).write_text(json.dumps(manifest, ensure_ascii=False),
                                       encoding="utf-8")
    except Exception:
        pass
    return True, "已更新 %d 个文件（重启后生效），用户数据未动" % n


def perform_update(root: Path = None, on_event=None) -> dict:
    """完整流程：查更新 → 下载 → 覆盖 → 返回结果（由 GUI 决定何时提示/重启）。

    on_event(type, text) 可传一个回调用于界面实时显示进度。
    返回 {"ok": bool, "message": str, "need_restart": bool}
    """
    root = Path(root or _install_root())

    def _ev(t, text=""):
        if on_event:
            try:
                on_event(t, text)
            except Exception:
                pass

    _ev("checking", "正在检查更新…")
    chk = check_update(root)
    if not chk.get("has_update"):
        _ev("done", "已是最新版本" if not chk.get("reason") else "检查更新失败（可忽略）")
        return {"ok": True, "message": "已是最新版本（无需更新）", "need_restart": False}
    url = chk.get("url")
    if not url:
        _ev("done", "远端没有可用的更新包")
        return {"ok": False, "message": "远端没有可用的 .zip 更新包", "need_restart": False}

    _ev("downloading", "下载更新包 %s …" % chk.get("version", ""))
    tmp_dir = Path(tempfile.mkdtemp(prefix="xrz_upd_"))
    tmp_zip = tmp_dir / "update.zip"
    if not _download(url, tmp_zip):
        _ev("error", "下载失败（网络？）")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"ok": False, "message": "下载更新包失败（请检查网络后重试）", "need_restart": False}

    _ev("installing", "正在安装更新…")
    ok, msg = apply_update(tmp_zip, root)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if not ok:
        _ev("error", msg)
        return {"ok": False, "message": msg, "need_restart": False}
    _ev("done", msg + "（重启软件后生效）")
    return {"ok": True, "message": msg, "need_restart": True}


def restart_self(root: Path = None) -> None:
    """重启当前运行方式（PyInstaller 模式重启 exe；源码模式重开 backend + shell）。"""
    root = Path(root or _install_root())
    env = os.environ.copy()
    try:
        if getattr(sys, "frozen", False):
            # PyInstaller onedir：当前可执行文件
            p = subprocess.Popen(
                [sys.executable],
                cwd=str(Path(sys.executable).parent),
                env=env,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | 0x00000208,
            )
            os._exit(0)
        else:
            # 源码模式：重开 backend（terminal.py）+ 让用户重开 GUI 壳
            term = root / "terminal.py"
            env["XRZ_NO_GUI"] = "1"
            subprocess.Popen(
                [sys.executable, str(term)],
                cwd=str(root),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008,
            )
    except Exception:
        pass
