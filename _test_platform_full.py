# -*- coding: utf-8 -*-
"""
豆包平台全链路验证：
1) 抓 /events SSE（原始 socket）
2) 发一个强制要求 browser_research（子代理）的任务
3) 轮询 GUI（测试桥 9333）：子代理卡 / 消息数 / 页面重载标记
4) 检测到新子代理卡 → 自动截图
5) ai_final_reply → 最终截图 + 报告
"""
import socket, json, time, threading, urllib.request, sys, os

PLAT = sys.argv[1] if len(sys.argv) > 1 else 'doubao'
BASE = 'http://127.0.0.1:8888'
BRIDGE = 'http://127.0.0.1:9333'
TOKEN = '%s_SA_%d' % (PLAT.upper(), int(time.time()))
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_test_%s_report.json' % PLAT)
SHOT_DIR = os.path.dirname(os.path.abspath(__file__))

events = []          # (ts, obj)
ev_lock = threading.Lock()
stop_evt = threading.Event()


def sse_capture():
    try:
        s = socket.create_connection(('127.0.0.1', 8888), timeout=10)
        s.sendall(b'GET /events HTTP/1.1\r\nHost: 127.0.0.1:8888\r\nAccept: text/event-stream\r\n\r\n')
        s.settimeout(2.0)
        buf = b''
        while not stop_evt.is_set():
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                line = line.strip()
                if line.startswith(b'data:'):
                    payload = line[5:].strip()
                    try:
                        obj = json.loads(payload.decode('utf-8', 'replace'))
                        with ev_lock:
                            events.append((time.time(), obj))
                    except Exception:
                        pass
    except Exception as e:
        with ev_lock:
            events.append((time.time(), {'__sse_error': str(e)}))


def http_post(url, obj, t=30):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=t).read().decode()


def gui_eval(js, t=10):
    try:
        req = urllib.request.Request(BRIDGE + '/eval', data=json.dumps({'js': js}).encode())
        r = urllib.request.urlopen(req, timeout=t)
        return json.loads(r.read().decode()).get('value', '')
    except Exception as e:
        return '__ERR__' + repr(e)[:80]


def gui_shot(name):
    try:
        p = os.path.join(SHOT_DIR, name)
        http_post(BRIDGE, {'kind': 'shot', 'path': p}, t=40)
        return p if os.path.exists(p) else ''
    except Exception:
        return ''


CARD_JS = r'''(() => {
  const cards = Array.from(document.querySelectorAll('.subagent-block')).map(c => ({
    q: ((c.querySelector('.sa-q')||{}).textContent||'').slice(0,150),
    status: ((c.querySelector('.sa-status')||{}).textContent||'').slice(0,50),
    tail: Array.from(c.querySelectorAll('.sa-body *')).filter(x=>x.children.length===0)
             .map(x=>(x.textContent||'').trim().slice(0,80)).filter(Boolean).slice(-4)
  }));
  return JSON.stringify({cards: cards,
                         msgs: document.querySelectorAll('#messages > *').length,
                         marker: window.__XRZ_TOK || null});
})()'''


def main():
    t0 = time.time()
    # 页面重载标记
    gui_eval("window.__XRZ_TOK = '%s'; 'ok'" % TOKEN)
    # 先清掉可能残留的旧子代理卡，避免误判（记录数量即可，不动 DOM）
    pre = gui_eval(CARD_JS)

    th = threading.Thread(target=sse_capture, daemon=True)
    th.start()
    time.sleep(1.5)
    started_at = time.time()

    prompt = ("请务必调用 browser_research 工具（它会启动子代理窗口去网页上做深度研究）完成下面任务，"
              "禁止你自己直接回答或使用你自带的联网搜索：研究 2026 年 GitHub 上 star 增长最快的 3 个 "
              "AI Agent 项目，给出项目名、star 数和增长原因。")
    try:
        resp = http_post(BASE + '/command', {'command': prompt})
    except Exception as e:
        resp = 'POST_ERR ' + repr(e)
    print('[CMD]', resp[:120], flush=True)

    accepted = '"accepted"' in resp or "'accepted'" in resp or 'accepted' in resp
    shots = []
    saw_card = None
    final_reply = None
    timeline = []
    deadline = time.time() + 8 * 60
    last_marker = TOKEN

    while time.time() < deadline:
        time.sleep(5)
        snap = gui_eval(CARD_JS)
        entry = {'t': round(time.time() - started_at, 1), 'snap': snap[:600]}
        timeline.append(entry)
        # 页面重载检测
        try:
            d = json.loads(snap)
            if d.get('marker') != last_marker:
                print('[WARN] GUI page reloaded, marker lost -> re-set', flush=True)
                gui_eval("window.__XRZ_TOK = '%s'; 'ok'" % TOKEN)
        except Exception:
            pass
        # 新子代理卡出现（有非空 query 的卡）→ 截一次图
        try:
            d = json.loads(snap)
            real = [c for c in d.get('cards', []) if c.get('q')]
            if real and saw_card is None:
                saw_card = real
                p = gui_shot('_%s_subagent_card.png' % PLAT)
                if p:
                    shots.append(p)
                print('[CARD]', json.dumps(real, ensure_ascii=False)[:400], flush=True)
        except Exception:
            pass
        # 完成检测
        with ev_lock:
            fin = [o for (ts, o) in events
                   if ts > started_at and isinstance(o, dict)
                   and o.get('type') == 'ai_final_reply']
        if fin:
            final_reply = fin[-1]
            time.sleep(8)   # 等 GUI 渲染
            p = gui_shot('_%s_final.png' % PLAT)
            if p:
                shots.append(p)
            break

    stop_evt.set()
    time.sleep(1)

    with ev_lock:
        post = [(ts, o) for (ts, o) in events if ts > started_at and isinstance(o, dict)]
    from collections import Counter
    types = Counter(o.get('type', '?') for _, o in post)
    sa_ids = []
    for _, o in post:
        d = o.get('data')
        if not isinstance(d, dict):
            continue
        sid = d.get('subagent_task_id')
        if sid and sid not in sa_ids:
            sa_ids.append(sid)
    final_text = ''
    if final_reply:
        final_text = str((final_reply.get('data') or {}).get('text', ''))[:400]

    report = {
        'token': TOKEN,
        'command_accepted': accepted,
        'event_counts_post_command': dict(types),
        'subagent_ids_post_command': sa_ids,
        'subagent_card_in_gui': saw_card,
        'final_reply_head': final_text,
        'screenshots': shots,
        'timeline_len': len(timeline),
        'page_reloaded': any('marker' not in json.loads(s['snap']) or
                             json.loads(s['snap']).get('marker') != TOKEN
                             for s in timeline if s['snap'].startswith('{')),
    }
    with open(REPORT, 'w', encoding='utf-8') as f:
        json.dump({'report': report, 'timeline': timeline}, f, ensure_ascii=False, indent=1)
    print('[REPORT]', json.dumps(report, ensure_ascii=False, indent=1)[:1800], flush=True)


if __name__ == '__main__':
    main()
