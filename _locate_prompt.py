# -*- coding: utf-8 -*-
"""定位 commander.py 里系统提示词字符串的定义位置（判断是否可注入桌面路径）。"""
import io

src = io.open('agent_core/commander.py', encoding='utf-8').read().split('\n')
out = []

out.append('--- 从 2202 往上找最近的字符串赋值起点 ---')
for i in range(2201, 1400, -1):
    s = src[i].strip()
    if s.endswith('"""') and not s.startswith('"""') and '=' in s:
        out.append('L%d: %s' % (i + 1, src[i][:140]))
        break

out.append('--- 含 prompt 赋值的行 ---')
for i, L in enumerate(src):
    if ('PROMPT' in L or 'prompt =' in L or 'prompt=' in L) and '=' in L:
        out.append('L%d: %s' % (i + 1, L.strip()[:140]))

io.open('_locate_prompt.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('written', len(out), 'lines')
