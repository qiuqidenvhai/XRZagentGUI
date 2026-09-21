#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针3：切换平台后枚举所有 chromium 的 user-data-dir，判断是否为独立上下文。"""
import json, time, urllib.request, re
import psutil

B = "http://127.0.0.1:8888"

def post(cmd):
    req = urllib.request.Request(B + "/command", data=json.dumps({"command": cmd}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try: return json.loads(urllib.request.urlopen(req, timeout=20).read().decode()).get("type")
    except Exception as e: return f"ERR:{e}"

def all_user_data_dirs():
    paths = set()
    for p in psutil.process_iter(["name", "cmdline"]):
        try: args = p.info.get("cmdline") or []
        except Exception: continue
        if p.info.get("name") not in ("chrome.exe", "headless_shell.exe"): continue
        for arg in args:
            m = re.search(r"--user-data-dir=(.+)", arg)
            if m:
                paths.add(m.group(1).strip().strip('"'))
    return sorted(paths)

log = {}
log["before"] = all_user_data_dirs()
print("切换前 user-data-dirs:", log["before"], flush=True)

for key in ["tongyi", "doubao", "yuanbao", "deepseek"]:
    t = post(f"切换到 {key} 平台")
    time.sleep(6)
    dirs = all_user_data_dirs()
    print(f"切换到 {key}: post={t} dirs={dirs}", flush=True)
    log[key] = {"post": t, "user_data_dirs": dirs}

with open(r"D:\软件\XianRenZhangAgent\_probe3.json", "w", encoding="utf-8") as f:
    json.dump(log, f, ensure_ascii=False, indent=2)
print("探针3完成 -> _probe3.json", flush=True)
