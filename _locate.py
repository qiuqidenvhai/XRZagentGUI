# -*- coding: utf-8 -*-
"""精确定位：当前后端 pid 4192 打开的日志文件 + 源图尺寸 + 图标缓存"""
import json, os, time, glob
import psutil

base = r'D:\软件\XianRenZhangAgent'
out = {}

# 1) pid 4192 打开的文件（找当前日志）
try:
    p = psutil.Process(4192)
    fms = []
    for fh in p.open_files():
        if fh.path.endswith('.log') or 'log' in os.path.basename(fh.path).lower():
            fms.append({'path': fh.path, 'size': os.path.getsize(fh.path) if os.path.exists(fh.path) else -1})
    out['pid4192_logs'] = fms
    out['pid4192_alive'] = True
except Exception as e:
    out['pid4192_alive'] = False
    out['pid4192_err'] = str(e)

# 2) 源图尺寸
for f in ['__xianrenzhang_icon.png', '__browser_cactus_icon.ico', '__icon_preview.png']:
    pth = os.path.join(base, f)
    if os.path.exists(pth):
        try:
            from PIL import Image
            im = Image.open(pth)
            out.setdefault('sources', {})[f] = {'size': im.size, 'mode': im.mode,
                                                'bytes': os.path.getsize(pth),
                                                'fmt': im.format}
        except Exception as e:
            out.setdefault('sources', {})[f] = {'err': str(e), 'bytes': os.path.getsize(pth)}

# 3) 图标缓存文件
cache = []
for d in [r'C:\Users\X.LAPTOP-CA1GJQE3\AppData\Local\Microsoft\Windows\Explorer',
          r'C:\Users\X.LAPTOP-CA1GJQE3\AppData\Local\Microsoft\Windows\IconCache']:
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.lower().startswith('icon') or f.lower() in ('thumbcache','thumbdb','iconcache'):
                cache.append({'f': f, 'path': os.path.join(d,f),
                              'size': os.path.getsize(os.path.join(d,f)),
                              'mtime': time.strftime('%H:%M:%S', time.localtime(os.stat(os.path.join(d,f)).st_mtime))})
out['icon_cache'] = sorted(cache, key=lambda x: -x['size'])[:20]

# 4) 桌面壳是否还在跑（pythonw 跑 desktop_app）
for p in psutil.process_iter(['name','cmdline','pid']):
    try:
        cmd = ' '.join(p.info.get('cmdline') or [])
        if 'desktop_app.py' in cmd:
            out.setdefault('desktop_shell', []).append({'pid': p.info['pid'],
                'ct': time.strftime('%H:%M:%S', time.localtime(p.create_time())),
                'cmd': cmd[:150]})
    except Exception:
        pass

# 5) 找最近的 backend log（全目录按 mtime）+ 尾部 40 行
cands = [f for f in os.listdir(base) if f.endswith('.log')]
cands.sort(key=lambda f: -os.stat(os.path.join(base,f)).st_mtime)
out['latest_log_names'] = cands[:8]
top = os.path.join(base, cands[0]) if cands else None
if top:
    lines = open(top, encoding='utf-8', errors='replace').readlines()
    out['top_log_name'] = cands[0]
    out['top_log_tail'] = ''.join(lines[-40:])[::-1] and '\n'.join(l[-200:] for l in lines[-40:])

print(json.dumps(out, ensure_ascii=False, indent=1))
