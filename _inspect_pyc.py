import marshal, types

with open(r"D:/软件/XianRenZhangAgent/terminal.pyc", "rb") as f:
    f.read(16)
    code = marshal.load(f)


def walk(co, depth=0, prefix=""):
    seen = set()
    for c in co.co_consts:
        if isinstance(c, types.CodeType) and c.co_name not in seen:
            seen.add(c.co_name)
            print(f"{'  '*depth}{c.co_name:<40} names~ {[n for n in c.co_names if 'event' in n.lower() or 'publish' in n.lower() or 'client' in n.lower() or 'stream' in n.lower()][:8]}")
            walk(c, depth + 1)


print("=== 模块级符号 ===")
for c in code.co_consts:
    if isinstance(c, types.CodeType):
        print(c.co_name)
walk(code)
