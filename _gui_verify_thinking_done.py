# -*- coding: utf-8 -*-
"""针对性验证用户诉求「任务结束后思考过程被压缩成一条细缝」：
模拟任务结束（thinking 块加 .done + endPlan），确认 thinking 块 body 仍展开、
高度正常、不是 collapsed 细缝；截图亲眼看。"""
import json, time, urllib.request, datetime

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
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_thinking_done_verify_%s.png" % ts

# 清基线
eval_js("(()=>{document.querySelectorAll('#messages .thinking-block').forEach(e=>e.remove());"
        "if(typeof resetThinking==='function')resetThinking();return 'baseline'})()")

# 模拟一个多行 thinking 块 + Agent 计划块，然后触发任务结束（.done）
eval_js("""(function(){
  showThinking('任务结束前：分析完成，PPT 已生成 5 页');
  showThinking('任务结束前：分析完成，PPT 已生成 5 页\\n输出路径已落盘并校验通过');
  if (typeof appendThinking === 'function') {
    appendThinking('调用 pptx_create 工具');
    appendThinking('生成 5 页内容并写入 gui_session');
  }
  // 模拟任务结束：thinking 块与计划块都加 .done
  endThinking();
  if (typeof endPlan === 'function') endPlan();
  return 'done-state-applied';
})()""")
time.sleep(0.5)

# 读 .done 态 thinking 块的展开情况（关键：不细缝）
state = eval_js(r"""(() => {
  const blocks = document.querySelectorAll('#messages .thinking-block');
  const out = {blocks: blocks.length, detail: []};
  blocks.forEach(b => {
    const body = b.querySelector('.think-body');
    const head = b.querySelector('.think-head');
    out.detail.push({
      is_done: b.classList.contains('done'),
      is_collapsed: b.classList.contains('collapsed'),   // 必须 false 才不是细缝
      head_h: head ? head.offsetHeight : 0,
      body_h: body ? body.offsetHeight : 0,               // >0 才非细缝
      body_text_len: body ? body.textContent.length : 0,
      block_h: b.offsetHeight
    });
  });
  // 细缝判定：collapsed 或 body 高度 <= head 高度
  out.any_fine_gap = out.detail.some(d => d.is_collapsed || d.body_h <= 0 || d.body_h <= d.head_h);
  return JSON.stringify(out);
})()""")
print("DONE-STATE:", state, flush=True)

# 截图亲眼看
shot(SHOT)
print("SHOT:", SHOT, flush=True)
