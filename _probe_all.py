# -*- coding: utf-8 -*-
"""一次到位的实测探针：
A) 图标真实渲染：把 ico 的 16/32/48/64/256 档解出像素，导出 PNG + 统计绿色/不透明占比
B) 浏览器进程现状：所有 chrome/headless_shell 的 user-data-dir（判断 deepseek 是否被双开）
C) 后端 health + 当前 platform
D) 找出后端日志文件位置（供后续 grep 报错源）
"""
import json, os, time, urllib.request
from PIL import Image
import psutil

base = r'D:\软件\XianRenZhangAgent'
out = {}

# A) icon pixels
ico = os.path.join(base, '__xianrenzhang_icon.ico')
im = Image.open(ico)
avail = []
cur = 0
seen = set()
while True:
    try:
        im.seek(cur)
    except Exception:
        break
    sz = im.size
    cur += 1
    if sz in seen:
        continue
    seen.add(sz)
    rgba = im.convert('RGBA')
    px = list(rgba.getdata())
    total = len(px)
    opaque = sum(1 for p in px if p[3] > 128)
    green = sum(1 for p in px if p[3] > 128 and p[1] > p[0] and p[1] > p[2] and p[1] > 60)
    # 非绿、非透明的其它不透明占比（背景色等）
    png = os.path.join(base, f'_ico_px_{sz[0]}.png')
    rgba.save(png)
    avail.append({'px': sz[0], 'opaque%': round(opaque/total*100,1),
                  'green%': round(green/total*100,1), 'preview': os.path.basename(png)})
out['icon_pixels'] = avail

# B) chrome procs + their profile
procs = []
for p in psutil.process_iter(['name','cmdline','create_time','pid']):
    try:
        n = (p.info['name'] or '').lower()
        if n in ('chrome.exe','headless_shell.exe','chromium.exe'):
            cmd = ' '.join(p.info.get('cmdline') or [])
            ud = ''
            if '--user-data-dir' in cmd:
                i = cmd.index('--user-data-dir')
                ud = cmd[i:i+80]
            procs.append({'name': p.info['name'], 'pid': p.info['pid'],
                          'ct': time.strftime('%H:%M:%S', time.localtime(p.info['create_time'])),
                          'ud': ud, 'brief': cmd[:120]})
    except Exception:
        pass
out['chrome_procs'] = procs
# 统计 deepseek profile 出现次数
dsc = sum(1 for x in procs if 'browser_profiles\\deepseek' in x['ud'] or 'browser_profiles/deepseek' in x['ud'])
out['deepseek_profile_proc_count'] = dsc

# C) health
try:
    out['health'] = json.loads(urllib.request.urlopen(base.replace(base,'http://127.0.0.1:8888') + '/health', timeout=5).read().decode())
except Exception as e:
    out['health'] = {'error': str(e)}

# D) log files
logs = []
for f in os.listdir(base):
    if f.endswith('.log') or 'log' in f.lower() and f.endswith(('.txt','.log')):
        logs.append({'f': f, 'size': os.path.getsize(os.path.join(base,f)),
                     'mtime': time.strftime('%H:%M:%S', time.localtime(os.stat(os.path.join(base,f)).st_mtime))})
out['log_files'] = sorted(logs, key=lambda x:-x['size'])[:15]

print(json.dumps(out, ensure_ascii=False, indent=1))
