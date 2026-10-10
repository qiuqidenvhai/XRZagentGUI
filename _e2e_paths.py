# -*- coding: utf-8 -*-
"""端到端验证：后端实际使用的 user_paths 模块在各种环境下都解析正确。

直接 import 后端真实用到的模块（和 commander 同一个），模拟不同电脑：
  A. 本机默认
  B. 别人的电脑：OneDrive 重定向 + 不同用户名
  C. 桌面被挪到 D 盘（非 ASCII 路径）
  D. 网络桌面
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

out = []
from agent_core import user_paths as up

# A. 本机
out.append('[A] 本机默认      : %s' % up.desktop_dir())
out.append('    存在: %s' % os.path.isdir(up.desktop_dir()))

# B/C/D. 模拟别人的电脑
cases = [
    ('B 别人的电脑(OneDrive+不同用户名)', r'C:\Users\zhangsan\OneDrive\Desktop'),
    ('C 非ASCII自定义盘', r'D:\我的桌面\项目产物'),
    ('D 网络桌面', r'\\NAS\share\desktop'),
    ('E 带空格/中文', r'C:\Users\李 明\OneDrive\桌面'),
]
for label, fake in cases:
    os.environ['XRZ_DESKTOP'] = fake
    got = up.desktop_dir()
    esc = up.escape_for_prompt(got)
    js = up.json_path(got)
    out.append('[%s]' % label)
    out.append('    设置     : %s' % fake)
    out.append('    解析     : %s  %s' % (got, 'PASS' if got == fake else 'FAIL'))
    out.append('    prompt转义: %s' % esc)
    out.append('    JSON     : %s' % js)
    # 关键：JSON 必须是合法字符串字面量
    import json as _j
    try:
        back = _j.loads(js)
        ok = back == got
        out.append('    JSON可解析: %s (%s)' % (back, 'PASS' if ok else 'FAIL'))
    except Exception as e:
        out.append('    JSON解析FAIL: %r' % e)
    # 模拟 commander 的拼接方式
    snippet = '"path":"%s\\\\test\\\\report.docx"' % js[1:-1]
    try:
        obj = _j.loads('{%s}' % snippet)
        ok = obj['path'] == got + '\\test\\report.docx'
        out.append('    拼进JSON  : %s' % obj['path'])
        out.append('    路径正确  : %s' % ('PASS' if ok else 'FAIL'))
    except Exception as e:
        out.append('    拼进JSON  : 解析失败 %r  <-- 提示词会坏掉!' % e)
    out.append('')

os.environ.pop('XRZ_DESKTOP', None)
out.append('[F] 清除覆盖后回到本机: %s' % up.desktop_dir())

io.open(os.path.join(ROOT, '_e2e_paths_out.txt'), 'w', encoding='utf-8').write('\n'.join(out))
print('done')
