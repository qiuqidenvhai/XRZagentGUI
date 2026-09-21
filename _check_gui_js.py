"""抽出 gui.html 里的 <script> 块，交给 node --check 做语法校验。

GUI 只有一个前端文件 gui.html，改完必须验语法：
QtWebEngine 遇到语法错是**整块脚本不执行**（表现为按钮全都没反应、
测试桥返回空串），比控制器报错还难查。
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = r"D:\软件\XianRenZhangAgent"
NODE = r"C:\Users\X.LAPTOP-CA1GJQE3\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"


def main():
    src = open(os.path.join(ROOT, "gui.html"), encoding="utf-8").read()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)
    print("script 块:", len(blocks))
    ok = True
    for i, b in enumerate(blocks):
        if not b.strip():
            continue
        p = os.path.join(tempfile.gettempdir(), "_xrz_gui_check_%d.js" % i)
        open(p, "w", encoding="utf-8").write(b)
        r = subprocess.run([NODE, "--check", p], capture_output=True, text=True)
        if r.returncode != 0:
            ok = False
            print("块 %d 语法错误:\n%s" % (i, r.stderr[:1500]))
        else:
            print("块 %d OK (%d 字符)" % (i, len(b)))
        try:
            os.remove(p)
        except Exception:
            pass
    print("== 语法校验 %s ==" % ("全部通过" if ok else "有错误"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
