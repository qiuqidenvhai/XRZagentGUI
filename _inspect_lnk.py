import os
import struct
import sys

# 【2026-10-06 修"写死桌面位置"】原来写死开发者桌面路径，换台电脑直接 FileNotFound
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from agent_core.user_paths import desktop_dir as _up_desktop
    LNK = os.path.join(_up_desktop(), '启动仙人掌.lnk')
except Exception:
    LNK = os.path.join(os.path.expanduser('~'), 'Desktop', '启动仙人掌.lnk')

data = open(LNK, 'rb').read()
print('LNK size:', len(data))

# crude scan for UTF-16 paths inside the shell link
import re
txt = data.decode('utf-16-le', errors='ignore')
cands = re.findall(r'[A-Za-z]:\\[^\x00-\x1f<>|]{3,200}', txt)
print('--- utf16 path candidates ---')
for c in dict.fromkeys(cands):
    print('  ', c)

txt2 = data.decode('latin-1', errors='ignore')
cands2 = re.findall(r'[A-Za-z]:\\[^\x00-\x1f<>|]{3,200}', txt2)
print('--- ansi path candidates ---')
for c in dict.fromkeys(cands2):
    print('  ', c)

print('--- mtime ---')
import datetime
print(datetime.datetime.fromtimestamp(os.path.getmtime(LNK)))
