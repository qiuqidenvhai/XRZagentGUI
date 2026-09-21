# -*- coding: utf-8 -*-
"""扫描 terminal.pyc 的字节码常量，找出「会被 .format() 格式化的含 JSON 大括号的模板」。

背景：后端启动时抛
    ValueError: Invalid format specifier '"done",...' for object of type 'str'
说明某段包含 {"tool":"done", ...} 的模板被 str.format() 处理了。
可编辑的 .py 里没有 .format(，所以嫌疑在 terminal.pyc。
"""
import marshal
import os
import dis
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYC = os.path.join(HERE, "terminal.pyc")

with open(PYC, "rb") as f:
    f.read(16)
    code = marshal.load(f)

print("terminal.pyc 顶层 code:", code.co_name, "consts=", len(code.co_consts))


def walk(c, path="<module>"):
    yield c, path
    for k in c.co_consts:
        if hasattr(k, "co_code"):
            yield from walk(k, path + "." + k.co_name)


suspicious = []
formatters = []

for c, path in walk(code):
    names = set(c.co_names)
    if "format" in names or "format_map" in names:
        formatters.append((path, c.co_name, sorted(names & {"format", "format_map"}),
                           c.co_firstlineno))
    for k in c.co_consts:
        if isinstance(k, str) and ("{" in k):
            has_json = ('"tool"' in k) or ("'tool'" in k) or ('"done"' in k)
            if has_json:
                suspicious.append((path, c.co_name, len(k), k))

print("\n=== 用到 .format / .format_map 的 code 对象 (%d 个) ===" % len(formatters))
for p in formatters[:60]:
    print("  %-58s 行%s" % (p[0][:58], p[3]))

print("\n=== 含 {\"tool\"}/{\"done\"} 的字符串常量 (%d 个) ===" % len(suspicious))
for path, cname, ln, s in suspicious[:40]:
    print("-" * 72)
    print("归属: %s  (len=%d)" % (path, ln))
    # 打印含 tool/done 附近的片段
    i = s.find('"tool"')
    if i < 0:
        i = s.find('"done"')
    lo = max(0, i - 160)
    hi = min(len(s), i + 260)
    print("片段: ...%s..." % s[lo:hi].replace("\n", "\\n"))
