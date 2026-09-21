# -*- coding: utf-8 -*-
"""探针：图标现状 + 后端状态 + lnk 指向"""
import os, json, time, hashlib, socket

base = r'D:\软件\XianRenZhangAgent'
out = {}

# 1) 所有 ico / 相关 png
rows = []
for f in os.listdir(base):
    p = os.path.join(base, f)
    if not os.path.isfile(p):
        continue
    fl = f.lower()
    if fl.endswith('.ico') or ('icon' in fl and fl.endswith('.png')):
        st = os.stat(p)
        rows.append({'f': f, 'size': st.st_size,
                     'md5': hashlib.md5(open(p, 'rb').read()).hexdigest(),
                     'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))})
rows.sort(key=lambda r: r['f'])
out['icon_files'] = rows

# 2) ico 结构自检（解析头，报 count/each entry 格式）
import struct
for r in rows:
    if not r['f'].endswith('.ico'):
        continue
    data = open(os.path.join(base, r['f']), 'rb').read()
    try:
        reserved, ico_type, count = struct.unpack_from('<HHH', data, 0)
        ents = []
        off = 6
        for i in range(count):
            w, h, colors, resv, planes, bpp, size, offset = struct.unpack_from('<BBBBHHII', data, off)
            off += 16
            ents.append({'w': w, 'h': h, 'bpp': bpp, 'bytes': size, 'off': offset,
                         'is_png': size > 0 and offset + 8 < len(data) and data[offset:offset+8] == b'\x89PNG\r\n\x1a\n'})
        r['ico_header'] = {'reserved': reserved, 'type': ico_type, 'count': count,
                           'entries': ents}
    except Exception as e:
        r['ico_header'] = f'PARSE_FAIL: {e}'
out['ico_struct'] = [r for r in rows if r['f'].endswith('.ico')]

# 3) lnk 指向
lnk = os.path.join(base, '启动仙人掌.lnk')
out['lnk_exists'] = os.path.exists(lnk)
if os.path.exists(lnk):
    out['lnk_mtime'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.stat(lnk).st_mtime))

# 4) 后端 8888
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
out['backend_8888'] = s.connect_ex(('127.0.0.1', 8888)) == 0
s.close()

# 5) 桌面壳 9333（测试桥）
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
out['bridge_9333'] = s.connect_ex(('127.0.0.1', 9333)) == 0
s.close()

# 6) chrome 进程里带 ico 相关？看有多少 chrome（浏览器窗口）
import psutil
chrome = []
for p in psutil.process_iter(['name', 'cmdline', 'create_time']):
    try:
        if p.info['name'] and p.info['name'].lower() in ('chrome.exe', 'headless_shell.exe'):
            cmd = ' '.join(p.info.get('cmdline') or [])
            chrome.append({'name': p.info['name'], 'pid': p.pid,
                          'ct': time.strftime('%H:%M:%S', time.localtime(p.info['create_time'])),
                          'brief': cmd[:150]})
    except Exception:
        pass
out['chrome_procs'] = chrome

print(json.dumps(out, ensure_ascii=False, indent=1))
