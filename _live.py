# -*- coding: utf-8 -*-
"""读真后端日志尾部 + 精确查 Playwright chromium 进程（按路径特征）"""
import json, os, time
import psutil

base = r'D:\软件\XianRenZhangAgent'
out = {}

# 1) 真后端日志尾部 60 行
log = os.path.join(base, 'xrz_data', 'logs', 'terminal.log')
if os.path.exists(log):
    lines = open(log, encoding='utf-8', errors='replace').readlines()
    out['log_total_lines'] = len(lines)
    out['log_mtime'] = time.strftime('%H:%M:%S', time.localtime(os.stat(log).st_mtime))
    # 尾 60 行
    out['log_tail'] = [l.rstrip() for l in lines[-60:]]
    # 最后一次 页面未初始化 / 消息发送失败 / 新对话 出现的位置 + 上下文
    for kw in ['页面未初始化', '消息发送失败', '浏览器重启失败', 'start_new_conversation', '新建对话', '浏览器已关闭', 'Target']:
        idxs = [i for i, l in enumerate(lines) if kw in l]
        if idxs:
            j = idxs[-1]
            out.setdefault('kw_last', {})[kw] = {
                'count': len(idxs), 'line': j+1,
                'ctx': ''.join(lines[max(0,j-4):j+1])[-500:]}
        else:
            out.setdefault('kw_last', {})[kw] = {'count': 0}

# 2) 精确查 Playwright chromium（命令行里含 playwright_browsers / headless_shell / chrome 且 user-data-dir 指向 browser_profiles）
procs = []
for p in psutil.process_iter(['name','cmdline','create_time','pid']):
    try:
        cmd = ' '.join(p.info.get('cmdline') or [])
        n = (p.info['name'] or '').lower()
        hit = ('browser_profiles' in cmd) or ('headless_shell' in n) or (n in ('chrome.exe','chromium.exe'))
        if hit:
            procs.append({'name': p.info['name'], 'pid': p.info['pid'],
                          'ct': time.strftime('%H:%M:%S', time.localtime(p.info['create_time'])),
                          'brief': cmd[:160]})
    except Exception:
        pass
out['playwright_procs'] = procs

# 3) 后端 4192 是否还活着
try:
    psutil.Process(4192)
    out['backend_4192_alive'] = True
except Exception:
    out['backend_4192_alive'] = False

# 4) 8888 当前监听者
import socket
s = socket.socket(); s.settimeout(1)
out['port_8888_open'] = s.connect_ex(('127.0.0.1',8888))==0
s.close()

print(json.dumps(out, ensure_ascii=False, indent=1))
