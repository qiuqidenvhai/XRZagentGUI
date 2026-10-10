# -*- coding: utf-8 -*-
"""针对性验证：挑一条【确实有产物】的对话删掉，确认产物一个都没少。

上一轮测的对话名下产物是 0 个，没真正覆盖到"有产物"这个关键场景。
本脚本从产物索引 conversation_artifacts.json 里找"产物最多"的对话来删。
"""
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
API = "http://127.0.0.1:8888"

out = []
npass = nfail = 0


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-44s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


from agent_core import xrz_paths
from _files_listing_fix import _conv_id_variants, _conversation_artifacts_path

# 1) 找产物最多的对话
#    注意：_registry_buckets(idx, cid) 签名是「取某个 cid 的 bucket」，
#    不是返回全量。这里直接遍历 registry 顶层 key 统计（更简单可靠）。
reg = {}
try:
    p = _conversation_artifacts_path()
    if p is not None and Path(p).is_file():
        reg = json.loads(Path(p).read_text(encoding="utf-8"))
except Exception as e:
    out.append("[warn] 读产物索引失败: %r" % e)

if not isinstance(reg, dict):
    reg = {}
out.append("产物索引里会话数: %d" % len(reg))
cands = []
for cid, val in reg.items():
    if isinstance(val, list):
        n = len(val)
    elif isinstance(val, dict) and isinstance(val.get("paths"), list):
        n = len(val["paths"])
    else:
        n = 0
    if n:
        cands.append((n, str(cid)))
cands.sort(reverse=True)
out.append("产物最多的前 5 个会话: %s" % cands[:5])
out.append("")

if not cands:
    out.append("[FATAL] 索引里没有任何带产物的对话，无法验证")
    io.open(os.path.join(ROOT, "_delconv2_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, no candidate")
    sys.exit(0)

target_id = cands[0][1]
# 取出它名下的产物清单
val = reg.get(target_id) or []
items = val if isinstance(val, list) else (val.get("paths") or [])
prod_paths = []
for it in items:
    if isinstance(it, str):
        prod_paths.append(it)
    elif isinstance(it, dict):
        prod_paths.append(it.get("path") or it.get("name") or "")
prod_paths = [p for p in prod_paths if p]
out.append("== 目标对话: %s ==" % target_id)
out.append("  名下产物 %d 个:" % len(prod_paths))
for p in prod_paths[:8]:
    out.append("     %s (存在=%s)" % (os.path.basename(p), os.path.isfile(p)))
out.append("")

# 记录删除前这些文件的状态
before_state = {}
for p in prod_paths:
    if os.path.isfile(p):
        before_state[p] = os.path.getsize(p)
out.append("  实际存在的产物文件: %d 个" % len(before_state))
out.append("")

# 2) 删
req = urllib.request.Request(API + "/conversations/" + target_id, method="DELETE")
try:
    r = _op.open(req, timeout=30)
    code, body = r.status, json.loads(r.read())
except urllib.error.HTTPError as e:
    code, body = e.code, {}
    try:
        body = json.loads(e.read())
    except Exception:
        pass

out.append("== DELETE /conversations/%s ==" % target_id)
out.append("  HTTP %s  %s" % (code, json.dumps(body, ensure_ascii=False)[:220]))
out.append("")

out.append("== 断言 ==")
chk("HTTP 200", code == 200, code)
chk("type == ok", body.get("type") == "ok", body.get("type"))

time.sleep(1.0)

# 索引里没了
tj = Path(xrz_paths.TASKS_INDEX_PATH)
still = False
if tj.exists():
    d = json.loads(tj.read_text(encoding="utf-8"))
    tasks = (d.get("tasks") if isinstance(d, dict) else d) or []
    still = any(isinstance(t, dict) and str(t.get("id")) in set(_conv_id_variants(target_id))
                for t in tasks)
    chk("该对话已从索引移除", not still, still)

# 历史上下文删了（用所有 id 变体找）
cdir = Path(xrz_paths.CONVERSATIONS_DIR)
left = []
for v in _conv_id_variants(target_id):
    for nm in (v + ".json", "conv_" + v + ".json"):
        if (cdir / nm).exists():
            left.append(nm)
chk("历史上下文 JSON 已删", not left, left)

# 🔴 核心：产物一个都不能少
missing = [p for p in before_state if not os.path.isfile(p)]
changed = []
for p, sz in before_state.items():
    if os.path.isfile(p) and os.path.getsize(p) != sz:
        changed.append(p)
chk("该对话名下产物【一个都没被删】", not missing,
    "丢失 %d 个: %s" % (len(missing), [os.path.basename(x) for x in missing[:5]]))
chk("产物内容大小未变", not changed,
    "变化: %s" % [os.path.basename(x) for x in changed[:5]])
out.append("  核对产物: %d 个" % len(before_state))

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
out.append("（本轮验证的是'有产物的对话被删后产物必须完好'这个关键场景）")
io.open(os.path.join(ROOT, "_delconv2_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d, 产物核对 %d 个" % (npass, nfail, len(before_state)))
