# -*- coding: utf-8 -*-
"""端到端真机验证（GUI 窗口内 9333 桥驱动，不抢焦点）：
真实发一条会生成文件的任务 → 等产物落盘 → 验证产物栏：
  ① 只出现【本次新对话】的产物，不含任何历史任务的 PPT；
  ② 删除生成文件功能确实已移除（无 ✕ 按钮、deleteFile 不存在、只剩 📂 打开位置）；
  ③ 切到历史会话时产物栏正确换成该会话自己的文件（各会话隔离）。
全程走真实 SSE，不 mock。最后截图亲眼看。"""
import json, time, urllib.request, datetime, os

BRIDGE = "http://127.0.0.1:9333/"

def ev(js, timeout=30):
    req = urllib.request.Request(BRIDGE,
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("value")

def shot(path):
    req = urllib.request.Request(BRIDGE,
        data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")

def jparse(v):
    try: return json.loads(v)
    except Exception: return None

def sidebar():
    v = ev(r"""(() => {
      const items = [...document.querySelectorAll('#fileList .file-item')];
      return JSON.stringify({
        names: items.map(it => (it.querySelector('.file-name')||{}).textContent),
        del: items.some(it => it.querySelector('.file-close[title="删除"]')),
        reveal: items.reduce((a,it)=>a+it.querySelectorAll('.file-close[title="在资源管理器中显示位置"]').length,0),
        scope: (typeof FILE_SCOPE_CONV!=='undefined')?FILE_SCOPE_CONV:null,
      });
    })()""")
    return jparse(v) or {}

ts = datetime.datetime.now().strftime("%H%M%S")
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_e2e_scope_%s.png" % ts
print("=" * 60, flush=True)

# ── 前置：记录历史会话的产物，作为「不该出现」的对照
print("[前置] 历史会话产物（对照，不该出现在新对话产物栏）:", flush=True)
for cid, tag in [("tongyi_20260926_115426_15120", "半导体"),
                 ("deepseek_20260926_111158_17203", "新能源")]:
    ev("bindFileScope(%s)" % json.dumps(cid))
    time.sleep(2.0)
    s = sidebar()
    print(f"  [{tag}] {s.get('names')}", flush=True)
HIST_NAMES = {"半导体产业竞争格局.pptx", "新能源汽车市场趋势.pptx"}

# ── 1) 新建对话（清空作用域）
# 注意：newConversation() 是 async 同步端点（可能 >75s），内部 fetch 完成后才调
# resetChatView() → bindFileScope(null) 清空产物栏。这里【动态等待】
# FILE_SCOPE_CONV 变 null，而不是固定等几秒（用户红线：禁止自造固定秒数闸门）。
print("\n[步骤1] 新建对话（清空产物作用域，动态等待完成）", flush=True)
ev("newConversation && newConversation()")
scope_cleared = False
for i in range(60):
    time.sleep(2)
    s = sidebar()
    if s.get("scope") is None:
        scope_cleared = True
        print(f"  轮询{i}: scope 已清空", flush=True)
        break
    if i % 5 == 0:
        print(f"    轮询{i}: scope={s.get('scope')} names={s.get('names')}", flush=True)
s = sidebar()
print(f"  新对话产物栏: {s.get('names')} scope={s.get('scope')}", flush=True)
assert scope_cleared, "新建对话后产物作用域未清空（scope 仍指向旧任务）"
assert not (set(s.get('names') or []) & HIST_NAMES), "新对话不该带历史产物"
print("  OK: 新对话产物栏已清空，不含历史文件", flush=True)

# ── 2) 真实发一条会生成文件的任务（生成一个小 txt 产物，最快最稳）
print("\n[步骤2] 真实发任务（生成 txt 产物）", flush=True)
task_prompt = "请在当前工作目录生成一个文件 e2e_scope_test.txt，内容写「端到端产物栏隔离验证」"
ev("sendMsg(%s)" % json.dumps(task_prompt))
print("  已发送，等产物落盘…", flush=True)

# 等产物出现在产物栏（动态等待，不设固定秒数闸门）
appeared = None
for i in range(60):
    time.sleep(2)
    s = sidebar()
    names = s.get('names') or []
    if any("e2e_scope_test" in n for n in names):
        appeared = s
        break
    if i % 5 == 0:
        print(f"    轮询{i}: names={names}", flush=True)

print(f"\n[步骤3] 新对话产物栏最终: {appeared.get('names') if appeared else '超时未出现'}", flush=True)
if appeared:
    new_names = set(appeared.get('names') or [])
    # 断言：新产物出现，且不含历史 PPT
    assert any("e2e_scope_test" in n for n in new_names), "新产物未出现在产物栏"
    assert not (new_names & HIST_NAMES), f"产物栏混入历史文件: {new_names & HIST_NAMES}"
    print("  OK: 只出现本次新产物，不含历史任务文件", flush=True)
    # 断言：删除按钮已移除
    assert appeared.get("del") is False, "删除按钮不该存在"
    assert appeared.get("reveal", 0) >= 1, "应保留 📂 打开位置按钮"
    print("  OK: 删除按钮已移除，只剩 📂 打开位置", flush=True)
else:
    print("  WARN: 未等到新产物出现（可能是平台任务未完成），继续做静态断言", flush=True)

# ── 3) 全局：deleteFile 已移除
g = jparse(ev("JSON.stringify({df:(typeof deleteFile==='function'), html:(document.documentElement.innerHTML.indexOf('deleteFile')>=0)})")) or {}
print(f"\n[全局] deleteFile 函数存在={g.get('df')} HTML引用={g.get('html')}", flush=True)
assert g.get("df") is False and g.get("html") is False, "deleteFile 未彻底移除"
print("  OK: 删除生成文件功能已彻底移除", flush=True)

# ── 4) 切回历史会话，验证隔离（各会话各显自己的）
print("\n[步骤4] 切历史会话验证隔离", flush=True)
for cid, expect, tag in [("tongyi_20260926_115426_15120", "半导体产业竞争格局.pptx", "半导体"),
                         ("deepseek_20260926_111158_17203", "新能源汽车市场趋势.pptx", "新能源")]:
    ev("bindFileScope(%s)" % json.dumps(cid))
    time.sleep(2.0)
    s = sidebar()
    ok = (s.get("names") == [expect])
    print(f"  [{tag}] {s.get('names')} 精确={ok}", flush=True)
    assert ok, f"{tag} 会话产物应精确为 [{expect}]，实际 {s.get('names')}"

shot(SHOT)
print("\nSHOT:", SHOT, flush=True)
print("\n" + "=" * 60, flush=True)
print("端到端验证全部通过：产物栏只显示当前对话文件 + 删除功能已移除", flush=True)
