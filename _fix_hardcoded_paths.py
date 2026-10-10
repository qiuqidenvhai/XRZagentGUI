# -*- coding: utf-8 -*-
"""把剩余自测脚本里写死的开发者桌面/项目路径改成动态解析。

逐个文件做精确替换，避免正则误伤。改完打印每个文件剩余的硬编码数量。
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# (文件, [(旧片段, 新片段), ...])
DYN_HEADER = (
    "# 【2026-10-06 修\"写死桌面位置\"】原来写死开发者桌面路径，换台电脑就跑到\n"
    "# 别人桌面上去、还可能目录不存在。改为按本机真实环境解析。\n"
    "try:\n"
    "    from agent_core.user_paths import desktop_dir as _up_desktop\n"
    "    {VAR} = Path(_up_desktop(create=True)) / \"test\"\n"
    "except Exception:\n"
    "    import os as _os\n"
    "    {VAR} = Path(_os.path.expanduser(\"~\")) / \"Desktop\" / \"test\"\n"
)

JOBS = [
    (
        "xrz_t1_probe.py",
        [
            (
                'OUT = Path(r"C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop\\test")',
                DYN_HEADER.replace("{VAR}", "OUT"),
            ),
        ],
    ),
    (
        "xrz_selftest.py",
        [
            (
                'TEST_ROOT = Path(r"C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop\\test")',
                DYN_HEADER.replace("{VAR}", "TEST_ROOT"),
            ),
        ],
    ),
]

log = []
for fn, pairs in JOBS:
    p = os.path.join(HERE, fn)
    if not os.path.isfile(p):
        log.append("%-22s SKIP(不存在)" % fn)
        continue
    s = io.open(p, encoding="utf-8").read()
    orig = s
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new)
            log.append("%-22s 已替换一处" % fn)
        else:
            log.append("%-22s !! 未找到待替换片段: %s" % (fn, old[:60]))
    if s != orig:
        io.open(p, "w", encoding="utf-8").write(s)
    left = s.count("X.LAPTOP-CA1GJQE3")
    log.append("%-22s 剩余硬编码: %d" % (fn, left))

# 剩余的：_inspect_lnk.py / xrz_func_test.py 只是调试输出/docstring，人工看一眼即可
for fn in ("_inspect_lnk.py", "xrz_func_test.py"):
    p = os.path.join(HERE, fn)
    if os.path.isfile(p):
        s = io.open(p, encoding="utf-8").read()
        for i, l in enumerate(s.split("\n"), 1):
            if "X.LAPTOP-CA1GJQE3" in l:
                log.append("%-22s L%d: %s" % (fn, i, l.strip()[:100]))

io.open(os.path.join(HERE, "_fix_paths_out.txt"), "w", encoding="utf-8").write("\n".join(log))
print("done")
