# -*- coding: utf-8 -*-
"""截图干净的启动状态 + 列出在跑的平台浏览器"""
import urllib.request, json, os, re, psutil

p = r'D:\软件\XianRenZhangAgent\_app_reopen_clean.png'
req = urllib.request.Request('http://127.0.0.1:9333',
                             data=json.dumps({'kind': 'shot', 'path': p}).encode())
urllib.request.urlopen(req, timeout=40)
print('shot exists:', os.path.exists(p))

plats = set()
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        if (proc.info['name'] or '').lower() in ('chrome.exe', 'headless_shell.exe'):
            cl = ' '.join(proc.info['cmdline'] or [])
            m = re.search(r'browser_profiles[\\/]+(\w+)', cl)
            if m:
                plats.add(m.group(1))
    except Exception:
        pass
print('platform browsers running:', sorted(plats) or 'NONE')
