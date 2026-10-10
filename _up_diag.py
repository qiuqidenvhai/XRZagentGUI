# -*- coding: utf-8 -*-
"""确认受管 python 能否在本目录正常跑脚本（不碰任何 Windows API）。"""
import io
import os
import sys

log = []
log.append('python  : %s' % sys.version.split()[0])
log.append('cwd     : %s' % os.getcwd())
log.append('profile : %s' % os.environ.get('USERPROFILE'))
try:
    import winreg
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
    ) as k:
        val, _ = winreg.QueryValueEx(k, 'Desktop')
    log.append('reg Desktop(raw)   : %s' % val)
    log.append('reg Desktop(expanded): %s' % os.path.expandvars(str(val)))
    log.append('exists             : %s' % os.path.isdir(os.path.expandvars(str(val))))
except Exception as e:
    log.append('REG ERROR: %r' % e)

io.open('_up_out.txt', 'w', encoding='utf-8').write('\n'.join(log))
print('OK')
