# -*- coding: utf-8 -*-
"""验证 2026-09-26 预览修复：侧边栏 collapsed 时点文件预览应自动展开并可见。
通过 9333 测试桥驱动真实 GUI（desktop_app.py），不抢焦点。"""
import json, re, time, urllib.request

def eval_js(js, timeout=15):
    req = urllib.request.Request('http://127.0.0.1:9333/',
        data=json.dumps({'kind': 'eval', 'js': js}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')

def shot(path):
    req = urllib.request.Request('http://127.0.0.1:9333/',
        data=json.dumps({'kind': 'shot', 'path': path}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    return urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')

def get_val(raw):
    m = re.search(r'"value":\s*(.*)', raw, re.S)
    return m.group(1).strip() if m else raw

SHOT = 'D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_preview_fix_shot.png'

PATH_TXT = 'D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session/conc_a.txt'

# 1) 强制折叠侧边栏，记录折叠态
js1 = r"""(() => {
  const sb = document.getElementById('fileSidebar');
  sb.classList.add('collapsed');
  const c = document.getElementById('filePreviewContainer');
  return JSON.stringify({
    collapsed: sb.classList.contains('collapsed'),
    container_w_when_collapsed: c ? c.offsetWidth : -1,
    container_h_when_collapsed: c ? c.offsetHeight : -1
  });
})()"""
print('STEP1 collapsed:', get_val(eval_js(js1)))

# 2) 在折叠态下调用 previewFile —— 预览修复核心：应自动展开侧边栏
js2 = "previewFile(" + json.dumps(PATH_TXT) + "); 'triggered'"
print('STEP2 trigger:', get_val(eval_js(js2)))

# 3) 轮询：等待 async fetch 完成 + 侧边栏自动展开 + 容器渲染内容
last = None
for _ in range(25):
    time.sleep(0.5)
    js3 = r"""(() => {
      const sb = document.getElementById('fileSidebar');
      const c = document.getElementById('filePreviewContainer');
      return JSON.stringify({
        collapsed_now: sb ? sb.classList.contains('collapsed') : null,
        auto_expanded: sb ? !sb.classList.contains('collapsed') : null,
        container_w: c ? c.offsetWidth : 0,
        container_h: c ? c.offsetHeight : 0,
        has_edit_view: !!document.querySelector('#editTextarea'),
        preview_text: (c && c.textContent ? c.textContent.slice(0,60) : '')
      });
    })()"""
    last = get_val(eval_js(js3))
    try:
        obj = json.loads(last)
    except Exception:
        obj = {}
    if obj.get('auto_expanded') and obj.get('container_h', 0) > 0 and obj.get('has_edit_view'):
        break
print('STEP3 after preview:', last)

# 4) 截图亲眼看
print('STEP4 shot:', shot(SHOT)[:200])
