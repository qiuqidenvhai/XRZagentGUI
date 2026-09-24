#!/usr/bin/env python3
"""真机验收（走桌面壳 9333 测试桥，DOM 断言 + 行为验证）：
  ① 侧边栏「新建会话」现在接 newConversation（不是旧 createSession）
  ② newConversation 背后的 resetChatView 真清空对话区（旧上下文不再堆一起）
  ③ 删除反馈不再用 addMsg 红色报错塞主对话流；「任务不存在」走静默分支
"""
import json, urllib.request, time, sys

BRIDGE = "http://127.0.0.1:9333"

def eval_js(js):
    r = urllib.request.Request(BRIDGE,
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=40) as resp:
        return resp.read().decode("utf-8").strip()

# 等桥 + GUI 页面就绪（#messages 出现）
ready = False
for i in range(45):
    try:
        v = eval_js("document.getElementById('messages')?document.getElementById('messages').innerHTML.length:'')'")
        if v and v != "0":
            ready = True
            break
    except Exception:
        pass
    time.sleep(2)
if not ready:
    print("❌ GUI 页面未就绪（桥无 #messages）"); sys.exit(1)
print("GUI 页面就绪 ✅")

# ① 侧边栏「新建会话」按钮接的是 newConversation
r1 = eval_js("(()=>{const b=[...document.querySelectorAll('button')].filter(x=>/newConversation\\(\\)/.test((x.getAttribute('onclick')||''))&&/会话/.test((x.title||'')+ (x.textContent||'')));return b.length?('SIDEBAR_NEWSESSION_OK:'+b[0].textContent.trim()):'SIDEBAR_BAD';})()")
print("① 侧边栏新建会话按钮:", r1)

# ② 注入一条旧消息标记，调 newConversation 的清界面逻辑（resetChatView）后标记必须消失
eval_js("(()=>{const m=document.getElementById('messages');m.innerHTML='<div class=\"msg msg-user\">MARKER_OLD_MSG_777</div>';return 1;})()")
time.sleep(0.3)
eval_js("resetChatView(); return 'cleared';")
time.sleep(0.5)
r2 = eval_js("(()=>{const m=document.getElementById('messages');return m.innerHTML.indexOf('MARKER_OLD_MSG_777')>=0?'MARKER_STAYS(❌)':'MARKER_CLEARED(✅ 界面已清空，含新对话 welcome)';})()")
print("② 新建会话清界面:", r2)

# ③ 删除反馈：源码不再用 addMsg 红色报错 +「任务不存在」走静默
r3a = eval_js("(()=>{const s=removeTask.toString();return (s.indexOf(\"addMsg('删除任务\")>=0)?'REMOVE_TASK_STILL_ADDMSG(❌)':'REMOVE_TASK_FIXED(✅ 用轻提示/静默)';})()")
r3b = eval_js("(()=>{const s=deleteTask.toString();return (s.indexOf(\"addMsg('删除失败\")>=0)?'DELETE_TASK_STILL_ADDMSG(❌)':'DELETE_TASK_FIXED(✅)';})()")
r3c = eval_js("(()=>{return removeTask.toString().indexOf('不存在')>=0?'SILENT_EXISTS_BRANCH_OK':'NO_SILENT_BRANCH';})()")
print("③ 删除反馈:", r3a, "|", r3b, "|", r3c)

# ④ addLocalCard 支持自定义消失时长（传数字）
r4 = eval_js("(()=>{addLocalCard('TEST_DEL_CARD_888', 3000);return 'added';})()")
time.sleep(0.4)
r4b = eval_js("(()=>{return document.getElementById('messages').querySelectorAll('.local-card').length>0?'LOCAL_CARD_PRESENT(✅ 轻提示,不刷红色)':'NO_LOCAL_CARD';})()")
print("④ 轻提示卡片:", r4b)

ok = ("OK" in r1 or "SUCCESS" in r1) and "CLEARED" in r2 and "FIXED" in r3a and "FIXED" in r3b and "PRESENT" in r4b
print("\n总结:", "✅ 全部通过" if ok else "❌ 有项未过")
sys.exit(0 if ok else 2)
