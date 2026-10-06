# -*- coding: utf-8 -*-
"""清掉 GUI 底部残留的 3 个 multi_del_*_122521.txt 附件（磁盘已删，DOM 还挂着）。"""
import json, time, urllib.request

def eval_js(js, timeout=20):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")

js = r"""(() => {
  const junk = ['multi_del_1_20260926_122521.txt','multi_del_2_20260926_122521.txt','multi_del_3_20260926_122521.txt'];
  const base = p => String(p).split(/[\\/]/).pop();
  if (typeof pendingAttachments !== 'undefined')
    pendingAttachments = pendingAttachments.filter(p => junk.indexOf(base(p)) < 0);
  if (typeof FILE_LIST !== 'undefined')
    FILE_LIST = FILE_LIST.filter(f => f && junk.indexOf(f.name) < 0);
  if (typeof renderAttachments === 'function') renderAttachments();
  if (typeof renderFileSidebar === 'function') renderFileSidebar();
  const still = [...document.querySelectorAll('#fileList .file-name, .attach-name, [class*=attach]')]
    .map(e => e.textContent).filter(t => /multi_del_/.test(t || ''));
  return JSON.stringify({removed:true, remaining_multi_del: still});
})()"""
print("cleanup:", eval_js(js), flush=True)
time.sleep(1.0)
left = eval_js(r"""(() => {
  const t = document.body.innerText || '';
  const m = t.match(/multi_del_[^ \n]*/g);
  return JSON.stringify({multi_del_mentions_in_ui: m || []});
})()""")
print("verify:", left, flush=True)
