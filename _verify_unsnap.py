# -*- coding: utf-8 -*-
"""真机验证「分屏之后还能把窗口拖走」（用户报的 bug）。

原 bug：贴边分屏后，再拖动窗口松手，它会自己跑回分屏时的位置（如左下角），
        表现为"被吸住、拖不动"。根因是取消分屏时 setGeometry(_snap_normal_geo)
        把窗口强行拉回【拖动开始前】的几何。

本测试在进程内模拟真实拖动序列（begin → move → commit），不走鼠标：
  1) 分屏到 bl（左下角）
  2) 再"拖"到屏幕中间并松手（不在热区）
  3) 断言窗口停在中间，而不是被拽回左下角
另外验证最小尺寸恢复不会把窗口突然撑大。
"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BRIDGE = "http://127.0.0.1:9333/"

out = []
npass = nfail = 0


def bridge(body, timeout=40):
    req = urllib.request.Request(BRIDGE, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"},
                                method="POST")
    return json.loads(_op.open(req, timeout=timeout).read()).get("value")


def snap(action="probe"):
    r = bridge({"kind": "snap", "action": action})
    try:
        return json.loads(r)
    except Exception:
        return {"error": r}


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-38s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


# 0) 桥就绪
d = None
for _ in range(60):
    try:
        d = snap("probe")
        if isinstance(d, dict) and "area" in d:
            break
    except Exception:
        time.sleep(2)
if not isinstance(d, dict) or "area" not in d:
    out.append("[FATAL] 桥未就绪: %r" % (d,))
    io.open(os.path.join(ROOT, "_unsnap_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, bridge not ready")
    sys.exit(0)

AX, AY, AW, AH = d["area"]
out.append("屏幕可用区: %dx%d@( %d,%d )" % (AW, AH, AX, AY))
out.append("")

# 1) 先分屏到左下角
out.append("== 1) 分屏到左下角（bl）==")
r = snap("bl")
time.sleep(1.0)
p = snap("probe")
cur = tuple(p.get("cur") or [])
want_bl = (AX, AY + AH // 2, AW // 2, AH - AH // 2)
chk("已分屏到左下角", cur == want_bl, "cur=%s want=%s" % (str(cur), str(want_bl)))
out.append("")

# 2) 模拟"从左下角拖到中间并松手" —— 关键回归点
#    新增桥动作 drag_to：模拟 begin → move(目标点) → _snap_update → commit
out.append("== 2) 分屏后拖到中间松手（不应被拽回）==")
target = (AX + AW // 2 - 200, AY + AH // 2 - 150)   # 屏幕中间偏上
r2 = bridge({"kind": "snap", "action": "drag_to",
             "tx": target[0], "ty": target[1]})
try:
    d2 = json.loads(r2)
except Exception:
    d2 = {"raw": r2}
time.sleep(1.0)
p2 = snap("probe")
cur2 = tuple(p2.get("cur") or [])
out.append("  拖动目标: %s" % (str(target),))
out.append("  实际结果: %s" % (str(cur2),))
# 窗口左上角应停在拖动目标附近，而不是 bl 的 (0, 547)
ok_moved = abs(cur2[0] - target[0]) <= 40 and abs(cur2[1] - target[1]) <= 40
chk("窗口停在拖动目标处（没被拽回左下角）", ok_moved,
    "cur=%s target=%s" % (str(cur2), str(target)))
chk("窗口尺寸仍是分屏尺寸（取消分屏不改变大小）",
    (cur2[2], cur2[3]) == want_bl[2:], "cur=%s want=%s" % (str(cur2[2:]), str(want_bl[2:])))

out.append("")
out.append("== 3) 最小尺寸恢复不应把窗口突然撑大 ==")
p3 = snap("probe")
out.append("  当前几何: %s" % (p3.get("cur"),))
out.append("  窗口仍小于 900x620 时，最小尺寸应保持推迟状态（不跳变）")
chk("几何未因恢复最小尺寸而突变成 900x620",
    tuple(p3.get("cur") or [])[2:] != (900, 620) or want_bl[2:] == (900, 620),
    "cur=%s" % (p3.get("cur"),))

# 4) 手动放大后应补上最小尺寸（_snap_flush_pending_min）
out.append("")
out.append("== 4) 手动放大到 1200x900 后补恢复最小尺寸 ==")
r4 = bridge({"kind": "snap", "action": "resize_to", "w": 1200, "h": 900})
time.sleep(1.2)
p4 = snap("probe")
cur4 = tuple(p4.get("cur") or [])
chk("窗口已放大到 1200x900", (cur4[2], cur4[3]) == (1200, 900), cur4)
r5 = bridge({"kind": "snap", "action": "min_info"})
try:
    d5 = json.loads(r5)
except Exception:
    d5 = {}
out.append("  最小尺寸信息: %s" % (d5,))
chk("最小尺寸已恢复为 900x620", d5.get("min_win") == [900, 620], d5.get("min_win"))

# 收尾
bridge({"kind": "snap", "action": "restore"})
time.sleep(0.8)

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
io.open(os.path.join(ROOT, "_unsnap_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
