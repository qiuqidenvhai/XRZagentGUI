# -*- coding: utf-8 -*-
"""仙人掌 GUI 真机验收（覆盖用户最新诉求）：
  - 📂 打开文件位置按钮（文件项 + 预览工具栏）
  - ⏹ 暂停 / 💬 插话 按钮：busy 态出现、点击⏹真实发 /interrupt
  - 引导对话(showOnboarding) 卡渲染
  - 子代理卡片(upsertSubagentCard) 渲染链路：start→流式→done
  - 截图留痕
走桌面壳测试桥（127.0.0.1:9333）。
"""
import json, os, time, urllib.request, urllib.error

BR = "http://127.0.0.1:9333"
API = "http://127.0.0.1:8888"
ROOT = r"D:\软件\XianRenZhangAgent"
DUMP = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_dumps")
os.makedirs(DUMP, exist_ok=True)

RESULTS = []


def log(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print("  [%s] %-30s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:240]), flush=True)


def ev(js, timeout=30):
    req = urllib.request.Request(
        BR + "/", data=json.dumps({"js": js, "kind": "eval"}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode()).get("value")
    except Exception as e:
        return "ERR:" + str(e)[:160]


def shot(name):
    path = os.path.join(DUMP, name)
    req = urllib.request.Request(
        BR + "/", data=json.dumps({"kind": "shot", "path": path}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
        return os.path.exists(path)
    except Exception as e:
        return "ERR:" + str(e)[:120]


def main():
    print("=== 仙人掌 GUI 真机验收 ===", flush=True)
    time.sleep(2)

    # 1) 函数存在性
    for fn in ["revealFile", "interruptTask", "insertMessage", "showOnboarding", "upsertSubagentCard", "addAttachment", "renderFileSidebar"]:
        log("fn:" + fn, ev("(typeof %s)==='function'" % fn), "")

    # 2) 📂 按钮 - 文件项（通过真实 addAttachment 入口渲染）
    ev("(function(){addAttachment('D:/软件/XianRenZhangAgent/terminal.py');return 'ok';})()")
    time.sleep(1)
    item = ev("""(function(){
        var b=document.querySelector(".file-item .file-close[onclick*=revealFile]");
        return b?b.getAttribute('onclick'):"NONE";})()""")
    log("fileitem_📂按钮", isinstance(item, str) and "revealFile" in item, item)

    # 3) 📂 按钮 - 预览编辑工具栏（调真实 previewFile）
    ev("previewFile('D:/软件/XianRenZhangAgent/terminal.py')")
    time.sleep(3)
    tb = ev("""(function(){
        var b=document.querySelector(".edit-toolbar button[onclick*=revealFile]");
        return b?b.outerHTML:"NONE";})()""")
    log("preview_📂按钮", isinstance(tb, str) and "打开位置" in tb, tb[:160] if isinstance(tb, str) else tb)

    # 4) ⏹/💬 busy 态显隐
    before = ev("getComputedStyle(document.getElementById('stopBtn')).display")
    ev("setStatus('busy')")
    time.sleep(0.5)
    busy_stop = ev("getComputedStyle(document.getElementById('stopBtn')).display")
    busy_ins = ev("getComputedStyle(document.getElementById('insertBtn')).display")
    log("busy_⏹显示", busy_stop == "flex", "before=%s busy=%s" % (before, busy_stop))
    log("busy_💬显示", busy_ins == "flex", busy_ins)

    # 5) 点⏹真实发 /interrupt（后端已验 200）；前后端联网
    #    先确认后端 /interrupt 通（避免误判）
    try:
        r = urllib.request.urlopen(urllib.request.Request(API + "/interrupt", method="POST"), timeout=6)
        log("interrupt_后端200", r.status == 200, r.read().decode()[:120])
    except Exception as e:
        log("interrupt_后端200", False, str(e)[:120])
    ev("interruptTask()")
    time.sleep(1)
    after = ev("getComputedStyle(document.getElementById('stopBtn')).display")
    log("click⏹_隐藏", after == "none", after)

    # 6) 引导对话卡
    ev("showOnboarding()")
    time.sleep(0.5)
    onb = ev("document.querySelectorAll('#messages .onboarding').length")
    onb_txt = ev("""(function(){var o=document.querySelector('#messages .onboarding');
        return o?o.innerText.replace(/\\s+/g,' ').slice(0,160):'NONE';})()""")
    log("引导对话卡", onb == 1, onb_txt)

    # 7) 子代理卡片渲染链路（桥只可靠回传标量，全部用标量断言；用唯一 id 避免跨次串味）
    import time as _t
    tid = "SA_DEMO_%d" % int(_t.time())
    ev("""(function(){
        upsertSubagentCard({subagent_task_id:'__TID__', subagent_query:'查一下北京天气', subagent_type:'start'}, 'task_started');
        return 'start';})()""".replace("__TID__", tid))
    time.sleep(0.5)
    sa_q = ev("""(function(){var c=document.querySelector('.subagent-block');
        return c&&c.querySelector('.sa-q')?c.querySelector('.sa-q').textContent:'NONE';})()""")
    sa_done0 = ev("""(function(){var c=document.querySelector('.subagent-block');
        return c?(c.className.indexOf('done')>=0):'NOCARD';})()""")
    log("子代理_card_start", isinstance(sa_q, str) and "北京天气" in sa_q and sa_done0 is False,
        "q=%s done0=%s" % (sa_q, sa_done0))

    ev("""(function(){
        upsertSubagentCard({subagent_task_id:'__TID__', subagent_type:'log', text:'正在检索气象数据...'}, 'subagent_log');
        upsertSubagentCard({subagent_task_id:'__TID__', subagent_done:true, subagent_type:'done'}, 'task_done');
        return 'done';})()""".replace("__TID__", tid))
    time.sleep(0.5)
    sa_done1 = ev("""(function(){var c=document.querySelector('.subagent-block');
        return c?(c.className.indexOf('done')>=0):'NOCARD';})()""")
    sa_lines = ev("document.querySelectorAll('.subagent-block .sa-line').length")
    log("子代理_card_done", sa_done1 is True and (isinstance(sa_lines, (int, float)) and sa_lines >= 1),
        "done=%s lines=%s" % (sa_done1, sa_lines))

    # 8) 截图留痕
    log("截图", shot("audit_new.png"))

    npass = sum(1 for r in RESULTS if r["ok"])
    print("\n=== 结果 %d/%d PASS ===" % (npass, len(RESULTS)), flush=True)
    open(os.path.join(ROOT, "_gui_audit_new_result.json"), "w", encoding="utf-8").write(
        json.dumps(RESULTS, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
