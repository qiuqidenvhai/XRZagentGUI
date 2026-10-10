# -*- coding: utf-8 -*-
"""验证最小尺寸是否真的恢复：尝试把窗口缩到 400x300，看是否被 900x620 拦住。

这是判定"最小尺寸有没有真恢复"的唯一可靠方式 —— minimumSize() 的读数
会被 QMainWindow 布局的 minimumSizeHint 干扰，不能直接当结论。
"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def bridge(body, timeout=40):
    req = urllib.request.Request("http://127.0.0.1:9333/",
                                data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"},
                                method="POST")
    return json.loads(_op.open(req, timeout=timeout).read()).get("value")


def snap(a="probe", **kw):
    b = {"kind": "snap", "action": a}
    b.update(kw)
    r = bridge(b)
    try:
        return json.loads(r)
    except Exception:
        return {"raw": r}


out = []
out.append("== 场景 A：正常状态（未分屏）==")
snap("restore")
time.sleep(1.0)
r = snap("resize_to", w=400, h=300)
time.sleep(1.0)
g = snap("probe").get("cur")
out.append("  请求缩到 400x300 → 实际 %s" % (g,))
clamped = (g[2], g[3]) != (400, 300)
out.append("  最小尺寸是否生效（被拦在 >=900x620）: %s  %s"
           % ("是" if clamped else "否 ← 最小尺寸丢了！", "PASS" if clamped else "FAIL"))

out.append("")
out.append("== 场景 B：分屏后再拖出，最小尺寸是否恢复 ==")
snap("restore")
time.sleep(0.8)
snap("bl")                      # 分屏左下角
time.sleep(1.0)
g1 = snap("probe").get("cur")
out.append("  分屏后几何: %s" % (g1,))
# 拖到中间松手 → 应恢复原始尺寸 1180x800
snap("drag_to", tx=500, ty=300)
time.sleep(1.2)
g2 = snap("probe").get("cur")
out.append("  拖出后几何: %s （期望含 1180x800 原始尺寸）" % (g2,))
ok_restore = (g2[2], g2[3]) == (1180, 800)
out.append("  恢复原始尺寸: %s  %s" % ("PASS" if ok_restore else "FAIL",
                                "" if ok_restore else "got=%s" % ((g2[2], g2[3]),)))
# 再试缩到 400x300，验证最小尺寸是否恢复
snap("resize_to", w=400, h=300)
time.sleep(1.0)
g3 = snap("probe").get("cur")
out.append("  缩到 400x300 → 实际 %s" % (g3,))
ok_min = (g3[2], g3[3]) != (400, 300)
out.append("  最小尺寸已恢复（被拦）: %s  %s" % ("PASS" if ok_min else "FAIL",
                                     "" if ok_min else "最小尺寸没恢复"))

snap("restore")
io.open(os.path.join(ROOT, "_minreal_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
