# -*- coding: utf-8 -*-
"""真机验证（GUI 窗口内 9333 桥驱动）：
① 产物栏只显示【指定对话】的文件（调 GUI 内部 bindFileScope(会话id)，就是点任务走的同一条路径）；
② 删除生成文件功能已移除（无 ✕ 按钮、deleteFile 不存在、只保留 📂 打开位置）。
对比验证：连续切两个不同会话，产物栏跟着换，且各自只含自己的文件。截图亲眼看。"""
import json, time, urllib.request, datetime

def ev(js, timeout=25):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("value")

def shot(path):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")

def jparse(v):
    try: return json.loads(v)
    except Exception: return None

SIDES = [
    ("tongyi_20260926_115426_15120", "半导体"),   # 半导体产业竞争格局.pptx
    ("deepseek_20260926_111158_17203", "新能源"),  # 新能源汽车市场趋势.pptx
]

for cid, tag in SIDES:
    ev("bindFileScope(%s)" % json.dumps(cid))
    time.sleep(2.5)
    v = ev(r"""(() => {
      const items = [...document.querySelectorAll('#fileList .file-item')];
      return JSON.stringify({
        names: items.map(it => (it.querySelector('.file-name')||{}).textContent),
        del: items.some(it => it.querySelector('.file-close[title="删除"]')),
        reveal: items.reduce((a,it)=>a+it.querySelectorAll('.file-close[title="在资源管理器中显示位置"]').length,0),
        scope: (typeof FILE_SCOPE_CONV!=='undefined')?FILE_SCOPE_CONV:null,
      });
    })()""")
    o = jparse(v) or {}
    print(f"[{tag}] scope={o.get('scope')} files={o.get('names')}")
    print(f"      删除按钮={o.get('del')} (应 False) | 📂打开位置={o.get('reveal')} 个", flush=True)

# 全局断言
g = jparse(ev("JSON.stringify({df: (typeof deleteFile==='function'), html_has_df: (document.documentElement.innerHTML.indexOf('deleteFile')>=0)})")) or {}
print("=== 全局断言 ===")
print("deleteFile 函数已移除:", g.get("df") is False, flush=True)
print("HTML 无 deleteFile 引用:", g.get("html_has_df") is False, flush=True)

ts = datetime.datetime.now().strftime("%H%M%S")
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_scope_final_%s.png" % ts
shot(SHOT)
print("SHOT:", SHOT, flush=True)
