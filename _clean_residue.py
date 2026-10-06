# -*- coding: utf-8 -*-
"""清掉多文件删除测试残留的 DOM 条目（上一轮 122521 三个文件已被删，
但 DOM 侧边栏还挂着；清掉 pendingAttachments + FILE_LIST 里这些项并重渲染）。"""
import json, urllib.request, time

def eval_js(js, timeout=45):
    req = urllib.request.Request("http://127.0.0.1:9333/",
        data=json.dumps({"kind": "eval", "js": js}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get("value")

resid = ["multi_del_1_20260926_122521.txt",
         "multi_del_2_20260926_122521.txt",
         "multi_del_3_20260926_122521.txt"]

js = ("(function(){var r=%s;"
      "pendingAttachments=pendingAttachments.filter(function(p){"
      "var b=String(p).split(/[\\\\/]/).pop();return r.indexOf(b)<0;});"
      "if(typeof FILE_LIST!=='undefined'){FILE_LIST=FILE_LIST.filter(function(f){"
      "return f&&r.indexOf(f.name)<0;});}"
      "renderFileSidebar();return 'cleaned'})()" % json.dumps(resid))
print("clean:", eval_js(js))
time.sleep(1.0)
names = json.loads(eval_js(
    "JSON.stringify([...document.getElementById('fileList')"
    ".querySelectorAll('.file-name')].map(function(e){return e.textContent}))"))
print("sidebar now:", json.dumps(names, ensure_ascii=False))
assert not any(n in names for n in resid), "残留未清干净"
print("PASS: 残留清理完成，侧边栏只剩真实 PPT 产物")
