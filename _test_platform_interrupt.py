# -*- coding: utf-8 -*-
"""
豆包平台 第二阶段：插话(/message) + 中断(/interrupt) 真机 GUI 验证。
全部走 GUI 真实路径（测试桥触发 onclick 处理函数），不直接打后端。
"""
import json, time, os, sys, urllib.request
PLAT = sys.argv[1] if len(sys.argv) > 1 else 'doubao'

BASE = 'http://127.0.0.1:8888'
BRIDGE = 'http://127.0.0.1:9333'
SHOT = os.path.dirname(os.path.abspath(__file__))
INSERT_TEXT = '插话测试：写完后请补一段浇水频率的具体建议'
results = {}


def http_post(url, obj=None, t=30):
    data = json.dumps(obj or {}).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=t).read().decode()


def gui_eval(js, t=10):
    try:
        req = urllib.request.Request(BRIDGE + '/eval', data=json.dumps({'js': js}).encode())
        r = urllib.request.urlopen(req, timeout=t)
        return json.loads(r.read().decode()).get('value', '')
    except Exception as e:
        return '__ERR__' + repr(e)[:80]


def shot(name):
    try:
        p = os.path.join(SHOT, name)
        http_post(BRIDGE, {'kind': 'shot', 'path': p}, t=40)
        return p if os.path.exists(p) else ''
    except Exception:
        return ''


STATE_JS = r'''(() => {
  const stopBtn = document.getElementById('stopBtn');
  const insertBtn = document.getElementById('insertBtn');
  const msgs = document.querySelectorAll('#messages > *');
  const last = msgs.length ? msgs[msgs.length-1].textContent.trim().slice(0,120) : '';
  return JSON.stringify({busy: stopBtn && stopBtn.style.display !== 'none',
                         insertVisible: insertBtn && insertBtn.style.display !== 'none',
                         nMsgs: msgs.length, lastMsg: last,
                         marker: window.__XRZ_TOK2 || null});
})()'''

# 1. 标记 + 确认空闲
gui_eval("window.__XRZ_TOK2 = 'P2_%d'; 'ok'" % int(time.time()))
st = json.loads(gui_eval(STATE_JS))
print('[IDLE]', st, flush=True)
if st.get('busy'):
    print('[ABORT] 后端已有任务在跑，等它结束再测', flush=True)
    sys.exit(2)

# 2. 发长任务（走 /command，与用户发送等价）
resp = http_post(BASE + '/command', {'command': '请写一篇 3000 字左右的《仙人掌科植物养护完全指南》，分章节，非常详细，慢慢写，不用急。'})
print('[CMD]', resp[:100], flush=True)
results['command_resp'] = resp[:120]

# 3. 等按钮出现（busy）
deadline = time.time() + 90
busy_seen = False
while time.time() < deadline:
    time.sleep(3)
    st = json.loads(gui_eval(STATE_JS))
    if st.get('busy') and st.get('insertVisible'):
        busy_seen = True
        break
results['busy_buttons_visible'] = busy_seen
print('[BUSY]', st, flush=True)
shot('_%s_busy_buttons.png' % PLAT)

if busy_seen:
    # 4. 插话：走真实 GUI 路径 —— 填输入框 + 调 insertMessage()
    js = ("document.getElementById('input').value = %s; insertMessage(); 'fired'" % json.dumps(INSERT_TEXT))
    gui_eval(js)
    time.sleep(2.5)
    st2 = json.loads(gui_eval(STATE_JS))
    queued = '插话已入队' in st2.get('lastMsg', '')
    results['insert_queued_msg'] = st2.get('lastMsg', '')
    results['insert_ok'] = queued
    print('[INSERT]', st2, flush=True)

    # 5. 中断：真实 GUI 路径
    gui_eval("interruptTask(); 'fired'")
    time.sleep(2.5)
    st3 = json.loads(gui_eval(STATE_JS))
    results['interrupt_msg'] = st3.get('lastMsg', '')
    results['interrupt_ok'] = '中断' in st3.get('lastMsg', '') or not st3.get('busy')
    print('[INTERRUPT]', st3, flush=True)
    shot('_%s_after_interrupt.png' % PLAT)

    # 6. 等任务真正结束（busy 消失，最多 5 分钟）
    deadline = time.time() + 300
    ended = False
    while time.time() < deadline:
        time.sleep(5)
        st4 = json.loads(gui_eval(STATE_JS))
        if not st4.get('busy'):
            ended = True
            break
    results['task_ended_after_interrupt'] = ended
    results['final_state'] = st4
    print('[END]', st4, flush=True)
    shot('_%s_after_end.png' % PLAT)

with open(os.path.join(SHOT, '_test_%s_interrupt_report.json' % PLAT), 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print('[REPORT]', json.dumps(results, ensure_ascii=False)[:600], flush=True)
