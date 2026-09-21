# -*- coding: utf-8 -*-
"""验证「会话落盘」修复：一次任务 = 一条历史记录，且标题是用户原话"""
import io, os, sys, json, time, glob
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import urllib.request as u
u.install_opener(u.build_opener(u.ProxyHandler({})))
sys.path.insert(0, r"D:\软件\XianRenZhangAgent")
import _xrz_harness as H

CONV = r"D:\软件\XianRenZhangAgent\xrz_data\XianRenZhang_tasks\conversations"


def tasks():
    d = json.loads(u.urlopen("http://127.0.0.1:8888/conversations", timeout=20).read().decode())
    return d.get("tasks", [])


def files():
    return glob.glob(os.path.join(CONV, "*.json"))


n0, f0 = len(tasks()), len(files())
print(f"运行前：历史条目={n0} 会话文件={f0}")

cmd = "帮我用一句话说明什么是事件循环，直接回答，不要调用任何工具"
print("发送命令:", cmd)
ok = H.post_command(cmd)
print("已受理:", ok)
time.sleep(45)

n1, f1 = len(tasks()), len(files())
print(f"运行后：历史条目={n1} 会话文件={f1}  (新增条目 {n1-n0} / 新增文件 {f1-f0})")

newest = max(files(), key=os.path.getmtime)
print("\n最新会话文件:", os.path.basename(newest))
data = json.loads(io.open(newest, encoding="utf-8").read())
for m in data.get("messages", [])[:6]:
    print(f"  [{m.get('role')}] {str(m.get('content'))[:100]}")

t = [x for x in tasks() if os.path.basename(newest) in (x.get("file") or "")]
print("\n该任务在历史里的标题:", (t[0].get("title") if t else "(未登记)"))
