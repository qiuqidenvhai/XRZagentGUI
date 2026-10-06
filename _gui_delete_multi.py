# -*- coding: utf-8 -*-
"""多文件删除测试（用户要求「删除功能要多个文件测试」）。
造 3 个唯一名 txt 到 gui_session → 驱动真实 GUI（9333 桥）让它们出现在侧边栏
→ 逐个点真实「删除」按钮（按 ✕ 文本匹配，避开 title 转义坑）→ 验证侧边栏逐条移除 + 磁盘 3 个都真删。
保留 PPT 等真实验收物，只删自己造的测试文件。"""
import json, time, os, urllib.request, datetime

def eval_js(js, timeout=45):
    data = json.dumps({"kind": "eval", "js": js}).encode()
    req = urllib.request.Request("http://127.0.0.1:9333/", data=data,
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("value")

ROOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks"
SESSION = os.path.join(ROOT, "gui_session")
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# 1) 造 3 个唯一名测试文件
names = [f"multi_del_{i}_{ts}.txt" for i in range(1, 4)]
paths = []
for n in names:
    p = os.path.join(SESSION, n)
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"多文件删除测试 {n}")
    paths.append(p.replace("\\", "/"))
print("created:", json.dumps(names, ensure_ascii=False), flush=True)

def sidebar_names():
    v = eval_js("JSON.stringify([...document.getElementById('fileList')"
                ".querySelectorAll('.file-name')].map(function(e){return e.textContent}))")
    try:
        return json.loads(v)
    except Exception:
        return []

# 2) override confirm + push 文件到 pendingAttachments
eval_js("window.confirm=function(){return true;};'confirm-ok'")

# 把路径 push 进 pendingAttachments 的 JS（一次建好）
push_all_js = ("(function(){var ps=%s;for(var i=0;i<ps.length;i++){"
               "if(pendingAttachments.indexOf(ps[i])<0) pendingAttachments.push(ps[i]);}"
               "renderFileSidebar();return 'pushed'})()" % json.dumps(paths))
print("push:", eval_js(push_all_js), flush=True)
time.sleep(1.0)
present = [n for n in names if n in sidebar_names()]
print("sidebar shows:", json.dumps(present, ensure_ascii=False), flush=True)

# 3) 逐个点真实「删除」按钮（按 ✕ 文本匹配；点击前确保该文件在侧边栏）
for n in names:
    # 确保该文件此刻在侧边栏（防止被其它渲染清掉）
    ensure_js = ("(function(){var ps=%s;if(pendingAttachments.indexOf(ps[0])<0)"
                 "pendingAttachments.push(ps[0]);renderFileSidebar();return 'ensured'})()"
                 % json.dumps([paths[names.index(n)]]))
    eval_js(ensure_js)
    time.sleep(0.3)
    # 按名称定位 item，取其文本为 ✕ 的删除按钮点击
    click_js = ("(()=>{var items=document.querySelectorAll('#fileList .file-item');"
                "for(var i=0;i<items.length;i++){"
                "var nm=items[i].querySelector('.file-name');"
                "if(nm&&nm.textContent===%s){"
                "var b=items[i].querySelector('.file-close');"
                "if(!b) return 'no-btn:'+'?';"
                "var closes=items[i].querySelectorAll('.file-close');"
                "var target=null;"
                "for(var j=0;j<closes.length;j++){ if(closes[j].textContent==='\\u2715'){ target=closes[j]; break; } }"
                "if(target){ target.click(); return 'clicked'; }"
                "return 'btn-no-x';"
                "}}"
                "return 'not-found:'+%s})()" % (json.dumps(n), json.dumps(n)))
    print("click:", eval_js(click_js), flush=True)
    gone = False
    for _ in range(24):
        time.sleep(1.5)
        if n not in sidebar_names():
            gone = True
            break
    disk_gone = not os.path.exists(os.path.join(SESSION, n))
    print("  ->", n, "sidebar_gone:", gone, "disk_gone:", disk_gone, flush=True)
    assert gone and disk_gone, f"{n} 删除验证失败"

# 4) 汇总
sidebar_now = sidebar_names()
results = {
    "tested_files": names,
    "all_gone_from_sidebar": all(n not in sidebar_now for n in names),
    "all_gone_from_disk": all(not os.path.exists(os.path.join(SESSION, n)) for n in names),
    "sidebar_remaining": [x for x in sidebar_now if not any(x == n for n in names)],
}
print("RESULT:", json.dumps(results, ensure_ascii=False, indent=2), flush=True)
assert results["all_gone_from_sidebar"] and results["all_gone_from_disk"], "多文件删除验证失败"
print("PASS: 多文件删除全部通过", flush=True)
