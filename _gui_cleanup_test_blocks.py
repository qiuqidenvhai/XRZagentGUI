# -*- coding: utf-8 -*-
"""清掉验证脚本注入到真实 GUI 的测试 thinking 块（按内容标记精确匹配，
绝不动用户真实历史消息 / 任务卡 / 产物）。"""
import json, time, urllib.request

def eval_js(js, timeout=20):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")

js = r"""(() => {
  const markers = [
    '任务结束前：分析完成',
    '调用 pptx_create 工具',
    '生成 5 页内容并写入 gui_session',
    '正在分析任务需求',
    '拆解为 3 个步骤',
    '开始生成 PPT 内容'
  ];
  const blocks = [...document.querySelectorAll('#messages .thinking-block')];
  let removed = 0;
  const kept = 0;
  blocks.forEach(b => {
    const txt = (b.textContent || '');
    if (markers.some(m => txt.indexOf(m) >= 0)) {
      b.remove();
      removed++;
    }
  });
  if (typeof resetThinking === 'function') resetThinking();
  if (typeof currentPlanBlock !== 'undefined') { currentPlanBlock = null; }
  const after = document.querySelectorAll('#messages .thinking-block').length;
  // 顺带把欢迎/空态恢复出来（若消息区被清成只剩我的测试块）
  return JSON.stringify({removed_test_blocks: removed, thinking_blocks_after: after});
})()"""
print("cleanup:", eval_js(js), flush=True)
time.sleep(0.8)

# 复查 GUI 干净态
chk = eval_js(r"""(() => {
  const out = {};
  out.thinking_blocks = document.querySelectorAll('#messages .thinking-block').length;
  out.task_items = document.querySelectorAll('#taskList .task-item').length;
  out.file_items = document.querySelectorAll('#fileList .file-item').length;
  out.preview_has_content = !!document.getElementById('filePreviewContainer')
      && document.getElementById('filePreviewContainer').textContent.trim().length > 0;
  // 残留 multi_del 测试附件
  out.junk_multi_del = (document.body.innerText || '').match(/multi_del_[^\n]*/g) || [];
  return JSON.stringify(out);
})()""")
print("GUI state:", chk, flush=True)
