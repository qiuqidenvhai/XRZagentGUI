import psutil, sys
killed = []
# 1) backend on port 8888
for c in psutil.net_connections(kind='tcp'):
    try:
        if c.status == 'LISTEN' and c.laddr.port == 8888 and c.pid:
            p = psutil.Process(c.pid)
            killed.append(('backend', c.pid, p.name()))
            p.kill()
    except Exception as e:
        print('port8888 err', e, flush=True)
# 2) all app-owned chrome browsers (cmdline contains browser_profiles)
for p in psutil.process_iter(['pid','name','cmdline']):
    try:
        info = p.info
        cl = info.get('cmdline') or []
        name = info.get('name') or ''
        if 'chrome' in name.lower() and any('browser_profiles' in (a or '') for a in cl):
            killed.append(('chrome', info['pid'], name))
            p.kill()
    except Exception:
        pass
print('KILLED:', killed, flush=True)
