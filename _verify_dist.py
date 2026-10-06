# -*- coding: utf-8 -*-
"""
_verify_dist.py —— 分发包装前/后交付校验（#86 收尾）。

校验项：
  1) 安全红线：整个分发包内（排除 runtime/agent_core/playwright 代码区）
     零登录态残留（cookie/login 文件名）。
  2) 包内 runtime 可独立 import 关键模块（socket/ssl/asyncio/playwright/
     PySide6/docx/pptx/PyPDF2/psutil），证明内置解释器完整、目标机免装 Python。
  3) 浏览器二进制在 xrz_data/playwright_browsers（与 xrz_paths 默认解析一致）。
  4) _version.json 存在且字段合理；启动仙人掌.bat 存在。
"""
import os
import sys
import json
import subprocess

DIST = r"D:\软件\XianRenZhangAgent\dist\XianRenZhangAgent"
FAILS = []


def _rel(path):
    return os.path.relpath(path, DIST).replace("\\", "/")


def scan_login_state():
    """返回疑似登录态/用户数据文件列表（代码区已排除）。"""
    bad = []
    for dirpath, dirs, files in os.walk(DIST):
        rel = _rel(dirpath)
        top = rel.split("/")[0] if rel != "." else "."
        # 代码 / 浏览器二进制：绝不误判
        if top in ("runtime", "agent_core") or rel.startswith("playwright") \
           or rel.startswith("xrz_data/playwright_browsers"):
            continue
        for f in files:
            low = f.lower()
            if "cookie" in low or "login" in low:
                bad.append(os.path.join(rel, f))
    return bad


def test_runtime_imports():
    py = os.path.join(DIST, "runtime", "python.exe")
    if not os.path.exists(py):
        FAILS.append("runtime/python.exe 缺失")
        return
    mods = ["socket", "ssl", "asyncio", "json", "http.server",
            "playwright", "PySide6", "docx", "pptx", "PyPDF2", "psutil"]
    code = ("import importlib\n"
            "ok=[]; bad=[]\n"
            "for m in %r:\n"
            "    try:\n"
            "        importlib.import_module(m); ok.append(m)\n"
            "    except Exception as e:\n"
            "        bad.append((m, repr(e)))\n"
            "print('IMPORT_OK', ','.join(ok))\n"
            "print('IMPORT_BAD', repr(bad))\n" % mods)
    try:
        out = subprocess.run([py, "-c", code], capture_output=True,
                             text=True, timeout=120, cwd=DIST)
    except Exception as e:
        FAILS.append("runtime import 测试异常: %r" % e)
        return
    for line in out.stdout.splitlines():
        if line.startswith("IMPORT_OK"):
            print("  包内 runtime 可 import:", line[len("IMPORT_OK"):])
        elif line.startswith("IMPORT_BAD"):
            bad = eval(line[len("IMPORT_BAD"):])
            if bad:
                FAILS.append("runtime import 失败: %r" % bad)
            else:
                print("  runtime import 全部通过 ✅")


def check_browser():
    bdir = os.path.join(DIST, "xrz_data", "playwright_browsers")
    if not os.path.isdir(bdir):
        FAILS.append("浏览器目录缺失: %s" % bdir)
        return
    has_chromium = any(n.startswith("chromium") or "chromium" in n
                       for n in os.listdir(bdir))
    if not has_chromium:
        FAILS.append("playwright_browsers 内未找到 chromium")
    else:
        print("  浏览器二进制就位:", bdir, "✅")


def check_meta():
    vj = os.path.join(DIST, "_version.json")
    if not os.path.exists(vj):
        FAILS.append("_version.json 缺失")
    else:
        try:
            v = json.load(open(vj, encoding="utf-8"))
            print("  _version.json:", v.get("version"), "download_url=",
                  repr(v.get("download_url")))
        except Exception as e:
            FAILS.append("_version.json 解析失败: %r" % e)
    bat = os.path.join(DIST, "启动仙人掌.bat")
    if not os.path.exists(bat):
        FAILS.append("启动仙人掌.bat 缺失")
    else:
        print("  启动仙人掌.bat 存在 ✅")
    # 根目录不应有后端运行残留
    for stray in ("_pkg_backend.log", "_pkg_backend2.log"):
        if os.path.exists(os.path.join(DIST, stray)):
            FAILS.append("分发包根目录含后端运行残留: %s" % stray)


def main():
    print("=== 分发包装交付校验 ===", flush=True)
    print("目标:", DIST, flush=True)
    if not os.path.isdir(DIST):
        print("❌ DIST 不存在，打包可能未完成")
        sys.exit(1)

    # 1) 安全红线
    bad = scan_login_state()
    if bad:
        FAILS.append("发现登录态/用户数据残留: %s" % bad[:10])
    else:
        print("  [安全] 零登录态残留 ✅")

    # 2) runtime import
    test_runtime_imports()

    # 3) browser
    check_browser()

    # 4) meta
    check_meta()

    print("\n" + "=" * 50)
    if FAILS:
        print("❌ 校验未通过，存在 %d 项问题：" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(2)
    print("✅ 全部交付校验通过")
    print("   分发包: %s" % DIST)
    print("   用法: 拷走整个 XianRenZhangAgent 文件夹 → 双击 启动仙人掌.bat")


if __name__ == "__main__":
    main()
