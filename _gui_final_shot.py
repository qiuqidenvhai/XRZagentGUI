# -*- coding: utf-8 -*-
"""最终干净态截图亲眼看：确认 GUI 无测试污染、真实任务/产物/预览正常。"""
import datetime, urllib.request

SHOT = r"D:/软件/XianRenZhangAgent/xrz_data/XianRenZhang_tasks/_gui_final_clean_%s.png" % datetime.datetime.now().strftime("%H%M%S")
req = urllib.request.Request("http://127.0.0.1:9333/",
    __import__("json").dumps({"kind": "shot", "path": SHOT}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
print("shot:", urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"), flush=True)
print(SHOT)
