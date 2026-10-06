# -*- coding: utf-8 -*-
"""等 GUI 任务列表加载，然后点开一个任务，验证产物栏只显示该任务文件。"""
import json, time, urllib.request

def ev(js, timeout=25):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("value")

def jparse(v):
    try:
        return json.loads(v)
    except Exception:
        return None

# 1) 等任务列表
n = 0
for _ in range(40):
    v = jparse(ev("JSON.stringify(document.querySelectorAll('#taskList .task-item').length)"))
    n = v or 0
    if n > 0:
        break
    time.sleep(1)
print("tasks loaded:", n, flush=True)
if n == 0:
    raise SystemExit("no tasks loaded")

# 2) 点开任务列表里带 PPT 产物描述的那个（找含「ppt」或「PPT」的任务项）
clicked = ev(r"""(() => {
  const items = [...document.querySelectorAll('#taskList .task-item')];
  // 优先点带 ppt 字样的任务
  let target = items.find(it => /ppt|PPT|报告|news|新闻/i.test(it.textContent||'')) || items[items.length-1];
  if (!target) return JSON.stringify({err:'none'});
  target.click();
  return JSON.stringify({clicked:(target.textContent||'').replace(/\s+/g,' ').slice(0,50), total:items.length});
})()""")
print("clicked:", clicked, flush=True)
time.sleep(3.5)

# 3) 断言产物栏
probe = ev(r"""(() => {
  const out = {};
  const items = [...document.querySelectorAll('#fileList .file-item')];
  out.file_count = items.length;
  out.file_names = items.map(it => (it.querySelector('.file-name')||{}).textContent);
  out.has_delete_btn = items.some(it => it.querySelector('.file-close[title="删除"]'));
  out.reveal_btns = items.reduce((a,it)=>a+it.querySelectorAll('.file-close[title="在资源管理器中显示位置"]').length,0);
  out.scope = (typeof FILE_SCOPE_CONV !== 'undefined') ? FILE_SCOPE_CONV : null;
  out.deleteFile_defined = (typeof deleteFile === 'function');
  return JSON.stringify(out);
})()""")
print("sidebar:", probe, flush=True)

obj = jparse(probe) or {}
# 判定
print("=== 判定 ===", flush=True)
print("deleteFile removed:", obj.get("deleteFile_defined") is False, flush=True)
print("no delete btn:", obj.get("has_delete_btn") is False, flush=True)
print("scoped to task:", bool(obj.get("scope")), "(scope=%s)" % obj.get("scope"), flush=True)
print("files shown:", obj.get("file_names"), flush=True)
