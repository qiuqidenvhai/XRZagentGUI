import marshal, dis, io
f=open('terminal.pyc','rb'); f.read(16); code=marshal.load(f)
def dump(c, name):
    buf=io.StringIO()
    print(f"=== {name} ===")
    for line in dis.Bytecode(c):
        print(line)
    for c2 in c.co_consts:
        if hasattr(c2,'co_code'):
            dump(c2, "nested:"+c2.co_name)
for c in code.co_consts:
    if hasattr(c,'co_code') and c.co_name in ('main','_main','cmd_loop','wait_for_http'):
        dump(c, c.co_name)
