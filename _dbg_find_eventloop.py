import marshal, dis, sys, importlib.util, types

HERE = r"D:\软件\XianRenZhangAgent"
with open(HERE + r"\terminal.pyc", "rb") as f:
    f.read(16)
    code = marshal.load(f)

TARGET = "事件循环未运行"

found = []

def walk(co, path=""):
    names = []
    # collect constants
    for c in co.co_consts:
        if isinstance(c, str):
            names.append(c)
    if TARGET in names:
        found.append((path or co.co_name, co))
    for c in co.co_consts:
        if hasattr(c, "co_code"):
            walk(c, path + "/" + c.co_name)

walk(code)

print("hits:", [p for p, _ in found])

for path, co in found:
    print("=" * 70)
    print("PATH:", path, "file:", co.co_filename, "line:", co.co_firstlineno)
    print("names:", co.co_names)
    print("varnames:", co.co_varnames)
    # print disassembly limited
    lines = []
    for ins in dis.get_instructions(co):
        lines.append(f"{ins.offset:5} {ins.opname:25} {ins.argrepr}")
    # find the offset where TARGET loaded
    tgt_off = None
    for ins in dis.get_instructions(co):
        if ins.argrepr and TARGET in str(ins.argrepr):
            tgt_off = ins.offset
            break
    if tgt_off is None:
        print("\n".join(lines[:200]))
    else:
        lo = max(0, tgt_off - 1200)
        hi = tgt_off + 400
        print("--- context ---")
        print("\n".join(l for l in lines if lo <= int(l.split()[0]) <= hi))
