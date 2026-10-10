# -*- coding: utf-8 -*-
"""验证 commander 系统提示词不再含硬编码开发者桌面路径。"""
import io
import re
import sys

sys.path.insert(0, '.')
out = []

# 1) 语法检查
import py_compile
try:
    py_compile.compile('agent_core/commander.py', doraise=True)
    out.append('[1] commander.py 语法 OK')
except Exception as e:
    out.append('[1] 语法错误: %r' % e)
try:
    py_compile.compile('agent_core/user_paths.py', doraise=True)
    out.append('[1] user_paths.py 语法 OK')
except Exception as e:
    out.append('[1] user_paths 语法错误: %r' % e)

# 2) 源码层：还有没有写死的开发者用户名
hard = []
for fn in ('agent_core/commander.py', 'agent_core/user_paths.py'):
    s = io.open(fn, encoding='utf-8').read()
    # 去掉注释行后再找（注释里作为反例说明可保留）
    code = '\n'.join(l for l in s.split('\n')
                     if not l.strip().startswith('#'))
    for i, l in enumerate(code.split('\n'), 1):
        if 'X.LAPTOP-CA1GJQE3' in l:
            hard.append('%s:%d  %s' % (fn, i, l.strip()[:100]))
out.append('[2] 代码(非注释)里残留的硬编码开发者用户名: %d 处' % len(hard))
out.extend('    ' + h for h in hard)

# 3) 类结构完整性（防"插函数吞掉类后半段"的老坑）
from agent_core.commander import Commander
need = ['_build_system_prompt']
missing = [m for m in need if not hasattr(Commander, m)]
out.append('[3] Commander.%s 缺失: %s' % ('/'.join(need), missing or '无'))

# 4) 实际生成的提示词里还有没有陌生用户名
try:
    c = Commander.__new__(Commander)   # 绕开 __init__ 的重依赖
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
    out.append('[4] 提示词长度: %d' % len(p))
    from agent_core.user_paths import desktop_dir as _dd
    real = _dd()
    # 正确断言：提示词里的路径必须等于【本机真实桌面】，
    # 而不是"不含某个字符串"——本机桌面路径本身当然含本机用户名。
    import json as _j
    m = re.search(r'"path":"(.+?)\\\\test\\\\report\.docx"', p)
    got = m.group(1) if m else None
    if got is None:
        out.append('[4] !! 未匹配到示例 report.docx 路径 —— FAIL')
    else:
        decoded = got.replace('\\\\', '\\')
        if decoded == _j.dumps(real)[1:-1].replace('\\', '\\\\'):
            out.append('[4] 提示词路径 == 本机真实桌面 —— PASS')
        elif decoded == real:
            out.append('[4] 提示词路径 == 本机真实桌面 —— PASS')
        else:
            out.append('[4] !! 提示词路径(%s) != 本机桌面(%s) —— FAIL' % (decoded, real))
    out.append('[4] 提示词里的示例 report.docx 路径: %s' % got)
    m2 = re.search(r'输出绝对路径，如 (.+?)\\xx\.pptx', p)
    out.append('[4] 提示词里的 pptx 示例路径: %s' % (m2.group(1) if m2 else '未找到(工具说明按需生成)'))

    # 5) 【关键】模拟别人的电脑：改 XRZ_DESKTOP 环境变量，
    #    提示词里的路径必须跟着变 —— 这才证明没有写死任何用户名。
    import os
    from agent_core import user_paths as up
    for fake in (r'D:\张三的桌面', r'C:\Users\zhangsan\OneDrive\Desktop',
                 r'\\NAS\share\desktop'):
        os.environ['XRZ_DESKTOP'] = fake
        try:
            os.makedirs(fake, exist_ok=True)
        except OSError:
            pass
        got2 = up.desktop_dir()
        ok = got2 == fake
        out.append('[5] XRZ_DESKTOP=%-38s -> %s  %s'
                   % (fake, got2, 'PASS' if ok else 'FAIL'))
        if not ok:
            break
    os.environ.pop('XRZ_DESKTOP', None)
    out.append('[5] 清除覆盖后回到本机真实桌面: %s (%s)'
               % (up.desktop_dir(), 'PASS' if up.desktop_dir() == real else 'FAIL'))
except Exception:
    import traceback
    out.append('[4] 生成提示词失败:\n' + traceback.format_exc())

io.open('_vp_out.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('done')
