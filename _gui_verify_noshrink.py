# -*- coding: utf-8 -*-
"""真机验证「思考块坍缩成紫色细线」修复（GUI 窗口内 9333 桥驱动）。
复现条件：消息区内容超高（overflow-y:auto 的 flex column 容器）→ 旧实现把
thinking 块纵向压扁成一条线。新实现 flex-shrink:0 后应保持正常高度、可展开。"""
import json, time, urllib.request, datetime

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

ts = datetime.datetime.now().strftime("%H%M%S")
SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_noshrink_%s.png" % ts

# ── 1) reload 载入新 CSS
ev("location.reload()")
time.sleep(4)
print("[1] reloaded", flush=True)

# ── 2) 制造超长消息区：塞大量气泡 + 造一个 thinking 块 + 标记 .done（模拟任务结束）
r = ev(r"""(() => {
  // 清空并塞 40 条长气泡，把消息区撑到超高（复现 flex 压缩场景）
  const md = document.getElementById('messages');
  md.innerHTML = '';
  for (let i = 0; i < 40; i++) {
    const d = document.createElement('div');
    d.className = 'msg msg-ai';
    d.textContent = '历史消息占位 ' + i + ' —— ' + '内容撑高测试。'.repeat(12);
    md.appendChild(d);
  }
  // 造一个 thinking 块（长思考内容），并标记 done（任务结束态）
  const wrap = document.createElement('div');
  wrap.className = 'thinking-block done';
  wrap.id = '__probe_think';
  const head = document.createElement('div');
  head.className = 'think-head';
  head.innerHTML = '<span class="dot"></span><span>🧠 模型推理（深度思考）</span>' +
    '<span class="think-hint" style="margin-left:auto">点击折叠</span><span class="chev">▾</span>';
  const body = document.createElement('div');
  body.className = 'think-body';
  body.textContent = '这是很长的思考内容。'.repeat(200);  // 长内容
  wrap.appendChild(head); wrap.appendChild(body);
  md.appendChild(wrap);
  return JSON.stringify({msg_children: md.children.length});
})()""")
print("[2] setup:", r, flush=True)
time.sleep(0.5)

# ── 3) 断言：thinking 块高度正常（不被压扁）+ 可展开/折叠
probe = ev(r"""(() => {
  const b = document.getElementById('__probe_think');
  const head = b.querySelector('.think-head');
  const body = b.querySelector('.think-body');
  const out = {
    block_h: b.offsetHeight,
    head_h: head.offsetHeight,
    body_h: body.offsetHeight,
    head_visible: head.offsetHeight >= 20,          // head 有正常高度（不是线）
    text_shown: head.textContent.includes('模型推理'), // 头部文字未被裁掉
    shrink: getComputedStyle(b).flexShrink,
  };
  // 折叠 → 展开
  head.click();
  out.after_collapse_h = b.offsetHeight;
  out.collapse_works = out.after_collapse_h < out.block_h;
  head.click();
  out.after_expand_h = b.offsetHeight;
  out.expand_restores = out.after_expand_h === out.block_h;
  out.body_text_intact = body.textContent.length;
  return JSON.stringify(out);
})()""")
p = jparse(probe) or {}
print("[3] probe:", json.dumps(p, ensure_ascii=False), flush=True)

shot(SHOT)
print("[4] SHOT:", SHOT, flush=True)

# ── 4) 判定
print("\n=== 判定 ===", flush=True)
print("flex-shrink 计算值:", p.get("shrink"), "(应 0)", flush=True)
print("head 有正常高度(>=20px):", p.get("head_visible"), "(head_h=%s)" % p.get("head_h"), flush=True)
print("头部文字未被裁掉:", p.get("text_shown"), flush=True)
print("可折叠:", p.get("collapse_works"), "| 可展开恢复:", p.get("expand_restores"), flush=True)
print("思考内容完整保留:", p.get("body_text_intact"), "字符", flush=True)
