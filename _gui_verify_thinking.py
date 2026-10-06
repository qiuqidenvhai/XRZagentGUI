# -*- coding: utf-8 -*-
"""真机验证 thinking 块行为（新 gui.html：默认展开、不自动坍缩、增量保留）
+ 预览「折叠态点文件自动展开」。驱动 9333 桥，不抢焦点。最后截图。"""
import json, time, os, urllib.request, datetime

def eval_js(js, timeout=20):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")

def shot(path):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_thinking_verify_%s.png" % ts

# 1) 清空 messages 里的临时 thinking 块，留干净基线
eval_js("(()=>{document.querySelectorAll('#messages .thinking-block').forEach(e=>e.remove());"
        "if(typeof resetThinking==='function')resetThinking();return 'baseline'})()")

# 2) 模拟 SSE ai_thinking 递增流（只许变长，含一个短残段）
frames = [
    "正在分析任务需求…",
    "正在分析任务需求…拆解为 3 个步骤…",
    "正在分析任务需求…拆解为 3 个步骤…开始生成 PPT 内容…",
    "END",  # 故意短残段，应被丢弃不坍缩
]
for fr in frames:
    eval_js("showThinking(%s)" % json.dumps(fr))
    time.sleep(0.2)

# 3) 读 thinking 块最终状态
state = eval_js(r"""(() => {
  const blk = document.querySelector('#messages .thinking-block');
  const body = blk ? blk.querySelector('.think-body') : null;
  const out = {};
  out.block_count = document.querySelectorAll('#messages .thinking-block').length;
  out.has_block = !!blk;
  out.is_collapsed = blk ? blk.classList.contains('collapsed') : null;   // 应 false（默认展开=不压缩）
  out.body_height = body ? body.offsetHeight : 0;                       // 展开时应 >0
  out.final_text = body ? body.textContent : '';
  out.kept_longest = body ? body.textContent.indexOf('开始生成 PPT 内容') >= 0 : false;
  out.no_collapse_to_residue = body ? body.textContent.indexOf('END') === -1 : true;
  return JSON.stringify(out);
})()""")
print("THINKING STATE:", state, flush=True)

# 4) 预览：强制折叠侧边栏 → 点一个文件 → 验证自动展开
js_preview = r"""(() => {
  const sb = document.getElementById('fileSidebar');
  sb.classList.add('collapsed');
  const items = document.querySelectorAll('#fileList .file-item');
  if (!items.length) {
    // 侧边栏空 → 注入一个测试文件到 pendingAttachments
    const p = 'D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/gui_session/TCP三次握手通俗解释.md';
    if (pendingAttachments.indexOf(p) < 0) pendingAttachments.push(p);
    renderFileSidebar();
  }
  const target = document.querySelector('#fileList .file-item');
  if (!target) return JSON.stringify({preview:'no-file-to-preview'});
  // 调它的 onclick 触发 previewFile
  target.click();
  return JSON.stringify({preview:'clicked', collapsed_before:true});
})()"""
print("PREVIEW TRIGGER:", eval_js(js_preview), flush=True)
time.sleep(1.5)
pv = eval_js(r"""(() => {
  const sb = document.getElementById('fileSidebar');
  const c = document.getElementById('filePreviewContainer');
  return JSON.stringify({
    auto_expanded: !sb.classList.contains('collapsed'),
    container_w: c ? c.offsetWidth : 0,
    container_h: c ? c.offsetHeight : 0,
    has_preview_content: c ? (c.textContent.trim().length > 0 || c.querySelector('img,iframe,.file-preview')) : false
  });
})()""")
print("PREVIEW RESULT:", pv, flush=True)

# 5) 截图亲眼看（让 thinking 块和预览都可见）
shot(SHOT)
print("SHOT:", SHOT, flush=True)
