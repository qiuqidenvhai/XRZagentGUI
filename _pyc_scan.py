# -*- coding: utf-8 -*-
"""反编译 terminal.pyc，dump 源码字符串，找 /command 路由、任务激活/恢复相关逻辑。"""
import dis, marshal, types, io, re, sys

PYC = r"D:\软件\XianRenZhangAgent\terminal.pyc"
out = {}

with open(PYC, "rb") as f:
    f.read(16)
    code = marshal.load(f)

# 收集所有可执行函数名
def walk(co, depth=0):
    names = []
    names.append((depth, co.co_name))
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            names.extend(walk(const, depth+1))
    return names

allfuncs = walk(code)
names = [n for _, n in allfuncs]
out["function_count"] = len(names)

# 找命令/恢复相关
keywords = ["command", "activate", "restore", "convers", "task", "new", "switch",
            "resume", "contin", "histor", "load_", "get_conversation", "do_POST",
            "do_GET", "start_new"]
hit = []
for n in names:
    if any(k in n.lower() for k in keywords):
        hit.append(n)
out["kw_funcs"] = sorted(set(hit))

# 找模块级字符串常量里含 "conversations" / "command" / "activate" 的
strhits = []
for c in code.co_consts:
    if isinstance(c, str) and any(k in c for k in ["activate", "resume", "restore_task",
                                                   "切回", "继续", "switch_task", "continue_task"]):
        strhits.append(c[:120])
out["module_str_consts"] = strhits[:40]

# dump 每个函数体里的 字符串常量 + 属性访问名（co_names），找 /command 处理与任务切换
route = {}
for _, nm in allfuncs:
    pass

# 找 do_POST / handle_command 之类的处理函数，dump 其 co_names（调用的方法名）和字符串
for c in code.co_consts:
    if isinstance(c, types.CodeType):
        if any(k in c.co_name.lower() for k in ["post", "command", "route", "handle", "event"]):
            route[c.co_name] = {
                "names": [n for n in c.co_names if not n.startswith("__")][:60],
                "strs": [s for s in c.co_consts if isinstance(s, str)][:60],
            }
out["route_funcs"] = route

# 把全量函数名导出
with open(r"D:\软件\XianRenZhangAgent\_pyc_funcs.txt", "w", encoding="utf-8") as f:
    for d, n in allfuncs:
        f.write("  " * d + n + "\n")

import json
print(json.dumps(out, ensure_ascii=False, indent=1))
