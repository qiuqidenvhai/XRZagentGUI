# -*- coding: utf-8 -*-
"""只复验元宝未登录提示：check_login 不再误判「已登录」后应该立刻给出登录提示。"""
import sys, time
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
from _xrz_harness import health, run, switch_platform, deepseek_count

BASE = deepseek_count()
print("health:", health(), flush=True)

print("\n切到元宝…", flush=True)
t_switch = time.time()
switch_platform("yuanbao")
print(f"切换耗时 {time.time()-t_switch:.0f}s", flush=True)
time.sleep(3)

for i in (1, 2):
    t0 = time.time()
    r = run("请用 file_write 工具创建文件 yb_probe%d.txt，内容为「probe」" % i,
            timeout=120)
    el = time.time() - t0
    f = r["final"]
    clean = ("需要登录" in f) or ("未登录" in f)
    no_dump = ("Call log" not in f) and ("Locator.wait_for" not in f)
    ok = clean and no_dump and el < 60
    print(f"第{i}条: 耗时 {el:.0f}s | 干净提示={clean} | 无堆栈={no_dump} "
          f"→ [{'PASS' if ok else 'FAIL'}]", flush=True)
    print(f"   final={f[:160]!r}", flush=True)

switch_platform("deepseek"); time.sleep(2)
print(f"\nDeepSeek 实例数 = {deepseek_count()}（基线 {BASE}）", flush=True)
print("health:", health(), flush=True)
