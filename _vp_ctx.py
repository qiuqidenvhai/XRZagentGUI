# -*- coding: utf-8 -*-
"""在生成的提示词里定位开发者用户名出现的位置与上下文。"""
import io
import sys

sys.path.insert(0, '.')
from agent_core.commander import Commander

c = Commander.__new__(Commander)


class _FakeReg:
    def __init__(self):
        self._tools = {}

    def list_tools(self):
        return []


c._tools = _FakeReg()
c._session = None
c._running = False
c._work_dir = None
p = c._build_system_prompt()

out = []
idx = -1
hits = 0
while True:
    idx = p.find('X.LAPTOP-CA1GJQE3', idx + 1)
    if idx < 0:
        break
    hits += 1
    a = max(0, idx - 200)
    b = min(len(p), idx + 200)
    out.append('=== 命中 #%d @ %d ===' % (hits, idx))
    out.append(p[a:b].replace('\n', '\\n'))
    out.append('')

out.insert(0, '总命中: %d' % hits)
out.append('--- 是否含 report.docx: %s' % ('report.docx' in p))
out.append('--- 是否含 Desktop: %s' % ('Desktop' in p))
io.open('_vp_ctx.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done, hits=%d' % hits)
