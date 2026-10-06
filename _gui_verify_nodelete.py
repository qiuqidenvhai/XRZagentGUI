# -*- coding: utf-8 -*-
"""真机验证两件事（GUI 窗口内，9333 桥驱动，不抢焦点）：
① 删除生成文件功能已移除：侧边栏每个文件项只有「📂 打开位置」，无 ✕ 删除按钮；
   deleteFile 函数不存在。
② 产物栏只显示【当前对话】的文件，不是历史所有文件：点一个任务后，
   侧边栏文件数应等于该任务 /attachments 返回数（≤2），且不含其它任务的 PPT。
最后截图亲眼看。"""
import json, time, urllib.request, datetime

def ev(js, timeout=25):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")

def shot(path):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")

ts = datetime.datetime.now().strftime("%H%M%S")
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_nodelete_%s.png" % ts

# 1) 打开一个具体任务（点最近那个 tongyi「半导体」任务）
r = ev(r"""(() => {
  const items = [...document.querySelectorAll('#taskList .task-item')];
  if (!items.length) return JSON.stringify({err:'no tasks'});
  // 点最后一个（最新的 deepseek 今日新闻）
  const it = items[items.length-1];
  it.click();
  return JSON.stringify({clicked: (it.textContent||'').slice(0,40), total: items.length});
})()""")
print("open task:", r, flush=True)
time.sleep(3.0)

# 2) 断言产物栏状态
probe = r"""(() => {
  const out = {};
  const items = [...document.querySelectorAll('#fileList .file-item')];
  out.file_count = items.length;
  out.file_names = items.map(it => (it.querySelector('.file-name')||{}).textContent);
  // 每个文件项的按钮：只应有 📂（title=在资源管理器中显示位置），不应有删除 ✕
  out.has_delete_btn = items.some(it => it.querySelector('.file-close[title="删除"]'));
  out.reveal_btns = items.reduce((n,it) => n + it.querySelectorAll('.file-close[title="在资源管理器中显示位置"]').length, 0);
  out.scope = (typeof FILE_SCOPE_CONV !== 'undefined') ? FILE_SCOPE_CONV : null;
  out.deleteFile_defined = (typeof deleteFile === 'function');
  return JSON.stringify(out);
})()"""
print("sidebar:", ev(probe), flush=True)

# 3) 截图亲眼看
shot(SHOT)
print("SHOT:", SHOT, flush=True)
