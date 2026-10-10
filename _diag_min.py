# -*- coding: utf-8 -*-
"""诊断：分屏时到底是谁在卡最小尺寸（宽度被钉在 900）。"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def snap(action="probe"):
    req = urllib.request.Request("http://127.0.0.1:9333/",
                                data=json.dumps({"kind": "snap", "action": action}).encode(),
                                headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(_op.open(req, timeout=40).read()).get("value")
    try:
        return json.loads(r)
    except Exception:
        return {"raw": r}


out = []
out.append("屏幕: %s" % (snap("probe").get("area"),))
out.append("")
r = snap("left")     # 目标 857x1095，实测会变成 900x1095
out.append("== 贴 left（期望 857x1095）==")
for k in ("applied", "geom", "expect", "min_win", "min_cw", "min_view",
          "layout_min", "sizehint_min", "maximized"):
    out.append("  %-14s %s" % (k, r.get(k)))

out.append("")
out.append("== 贴 tl（期望 857x547，实测 900x620）==")
r2 = snap("tl")
for k in ("applied", "geom", "expect", "min_win", "min_cw", "min_view",
          "layout_min", "sizehint_min", "maximized"):
    out.append("  %-14s %s" % (k, r2.get(k)))

io.open(os.path.join(ROOT, "_diag_min_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
