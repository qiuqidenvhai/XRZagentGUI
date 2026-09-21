import os
import struct

LNK = r'C:\Users\X.LAPTOP-CA1GJQE3\Desktop\启动仙人掌.lnk'

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
