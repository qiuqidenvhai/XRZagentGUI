# -*- coding: utf-8 -*-
"""全貌：所有浏览器类进程 + deepseek profile 锁文件 + 后端最近报错上下文"""
import json, os, time, glob
import psutil

base = r'D:\软件\XianRenZhangAgent'
out = {}

# 1) 所有疑似浏览器进程（放宽匹配）
names = set()
for p in psutil.process_iter(['name','cmdline','create_time','pid','ppid']):
    try:
        n = (p.info['name'] or '').lower()
        if any(k in n for k in ('chrome','chromium','headless','msedge')):
            cmd = ' '.join(p.info.get('cmdline') or [])
            ud = ''
            if '--user-data-dir' in cmd:
                i = cmd.index('--user-data-dir')
                ud = cmd[i:i+90]
            names.add(p.info['name'])
            out.setdefault('browser_procs', []).append({
                'name': p.info['name'], 'pid': p.info['pid'], 'ppid': p.info['ppid'],
                'ct': time.strftime('%H:%M:%S', time.localtime(p.info['create_time'])),
                'ud': ud, 'brief': cmd[:100]})
    except Exception:
        pass
out['browser_proc_names'] = sorted(names)

# 2) 后端 python 进程（谁在跑 terminal.py）
for p in psutil.process_iter(['name','cmdline','create_time','pid']):
    try:
        cmd = ' '.join(p.info.get('cmdline') or [])
        if 'terminal.py' in cmd or 'desktop_app.py' in cmd:
            out.setdefault('backend_procs', []).append({
                'name': p.info['name'], 'pid': p.info['pid'],
                'ct': time.strftime('%H:%M:%S', time.localtime(p.info['create_time'])),
                'cmd': cmd[:200]})
    except Exception:
        pass

# 3) deepseek profile 锁文件 + SingletonLock 是否被占用
ds = os.path.join(base, 'xrz_data', '.xianrenzhang_agent', 'browser_profiles', 'deepseek')
locks = []
for f in os.listdir(ds) if os.path.isdir(ds) else []:
    if 'ingleton' in f or f == 'lock.file' or 'chrome_debug_port' in f or 'Chrome_Port' in f:
        locks.append({'f': f, 'size': os.path.getsize(os.path.join(ds,f)),
                     'mtime': time.strftime('%H:%M:%S', time.localtime(os.stat(os.path.join(ds,f)).st_mtime))})
out['deepseek_lock_files'] = locks

# 4) 找最近一次报错的日志（按 mtime 排序）+ 抓 "消息发送失败" 附近 8 行
cands = [f for f in os.listdir(base) if f.endswith('.log') and os.path.getsize(os.path.join(base,f)) > 0]
cands.sort(key=lambda f: -os.stat(os.path.join(base,f)).st_mtime)
ctx = []
for f in cands[:6]:
    try:
        lines = open(os.path.join(base,f), encoding='utf-8', errors='replace').readlines()
    except Exception:
        continue
    for i, ln in enumerate(lines):
        if '消息发送失败' in ln or '浏览器重启失败' in ln or '页面未初始化' in ln:
            ctx.append({'f': f, 'line': i+1,
                       'before': ''.join(lines[max(0,i-3):i])[-300:],
                       'at': ln.rstrip()[:300]})
            if len(ctx) > 12: break
    if len(ctx) > 12: break
out['error_contexts'] = ctx
out['latest_logs'] = cands[:8]

print(json.dumps(out, ensure_ascii=False, indent=1))
