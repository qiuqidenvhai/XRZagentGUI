#!/usr/bin/env python3
"""Launch desktop_app.py, wait, take screenshot, then kill it."""
import os, subprocess, time, psutil

base = r'D:/软件/XianRenZhangAgent'
log_path = os.path.join(base, '_desktop_app.log')
log = open(log_path, 'w')

env = os.environ.copy()
env['XRZ_GUI_BRIDGE'] = '1'
env['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
env['QTWEBENGINE_CHROMIUM_FLAGS'] = '--no-sandbox --disable-gpu --disable-dev-shm-usage'
env['PYTHONUNBUFFERED'] = '1'

proc = subprocess.Popen(
    ['D:/软件/Python/pythonw.exe', 'D:/软件/XianRenZhangAgent/desktop_app.py'],
    cwd=base,
    env=env,
    stdout=log,
    stderr=subprocess.STDOUT,
)
print('LAUNCHED_PID', proc.pid)

# wait for bridge or death
bridge_ready = False
for i in range(20):
    time.sleep(1)
    rc = proc.poll()
    if rc is not None:
        print('EXITED', rc)
        break
    for c in psutil.net_connections(kind='tcp'):
        if c.laddr.port == 9333:
            bridge_ready = True
            print('BRIDGE_READY')
            break
    if bridge_ready:
        break
else:
    print('TIMEOUT_WAITING_BRIDGE')

log.flush()

# take screenshot via PIL
try:
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.save(os.path.join(base, '_desktop_test_screenshot.png'))
    print('SCREENSHOT', img.size)
except Exception as e:
    print('SCREENSHOT_ERR', e)

# kill desktop app and children
try:
    p = psutil.Process(proc.pid)
    for child in p.children(recursive=True):
        try: child.terminate()
        except: pass
    try: p.terminate()
    except: pass
except Exception as e:
    print('KILL_ERR', e)

log.close()
print('DONE')
