# -*- coding: utf-8 -*-
"""实测：删除历史对话应成功，且【产物文件必须保留】（用户的明确要求）。

步骤：
  1) 记录删除前状态：tasks.json 条目数、会话 JSON 数、产物文件数与总字节
  2) 造一条测试对话（或挑一条真实的）
  3) DELETE /conversations/<id>
  4) 断言：
     a) HTTP 200（不再是 403「文件名无法定位…」）
     b) 该 id 从 tasks.json 索引里消失
     c) 对应的历史上下文 JSON 被删
     d) **产物文件数量与字节数完全没变**（最关键的断言）
"""
import io
import json
import os
import shutil
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
    out.append("  [%s] %-42s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:130]))


def get(p, timeout=20):
    return json.loads(_op.open(API + p, timeout=timeout).read())


def delete(p, timeout=30):
    req = urllib.request.Request(API + p, method="DELETE")
    try:
        r = _op.open(req, timeout=timeout)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


from agent_core import xrz_paths

TJ = xrz_paths.TASKS_INDEX_PATH
CDIR = xrz_paths.CONVERSATIONS_DIR
TASKS_DIR = xrz_paths.TASKS_DIR if hasattr(xrz_paths, "TASKS_DIR") else None


def snapshot_products():
    """统计【产物文件】（递归），用于证明删除对话没有动它们。

    【口径修正】必须排除 conversations/ 目录下的 conv_*.json ——
    那是"历史上下文"，本来就属于该删的元信息。之前把它算进产物，
    导致每次删对话都误报"产物少了一个"（3 个假 FAIL）。
    """
    files = []
    roots = [xrz_paths.TASKS_DIR] if TASKS_DIR else []
    conv_dir = Path(CDIR).resolve()
    for root in roots:
        try:
            rp = Path(root)
            if not rp.is_dir():
                continue
            for p in rp.rglob("*"):
                try:
                    if not p.is_file() or "__pycache__" in str(p):
                        continue
                    # 排除历史上下文 JSON（该删，不是产物）
                    if conv_dir in p.resolve().parents or p.resolve() == conv_dir:
                        continue
                    files.append((str(p), p.stat().st_size))
                except (OSError, AttributeError):
                    pass
        except Exception:
            pass
    return files


from pathlib import Path

before = snapshot_products()
out.append("删除前产物: %d 个文件, 总 %d 字节" % (len(before), sum(s for _, s in before)))

tasks_before = []
if Path(TJ).exists():
    d = json.loads(Path(TJ).read_text(encoding="utf-8"))
    tasks_before = (d.get("tasks") if isinstance(d, dict) else d) or []
out.append("删除前索引条目: %d" % len(tasks_before))

conv_before = list(Path(CDIR).glob("*.json")) if Path(CDIR).is_dir() else []
out.append("删除前会话 JSON: %d 个" % len(conv_before))
out.append("")
target = None
for t in tasks_before:
    if isinstance(t, dict) and t.get("id"):
        target = str(t["id"])
        break
if not target:
    out.append("[FATAL] 索引里没有任何对话可删")
    io.open(os.path.join(ROOT, "_delconv_out.txt"), "w", encoding="utf-8").write("\n".join(out))
    print("done, no target")
    sys.exit(0)

out.append("== 删除目标: %s ==" % target)

# 先记下这条对话关联的产物（用于逐个核对还在不在）
name = target
prod_of_target = [(p, s) for p, s in before if name in Path(p).name or name in p]
out.append("  该对话名下产物: %d 个" % len(prod_of_target))
out.append("")

# 执行删除
code, data = delete("/conversations/" + target)
out.append("== DELETE /conversations/%s ==" % target)
out.append("  HTTP %s  响应: %s" % (code, json.dumps(data, ensure_ascii=False)[:260]))
out.append("")

out.append("== 断言 ==")
chk("HTTP 200（不再是 403）", code == 200, code)
chk("type == ok", data.get("type") == "ok", data.get("type"))
chk("不再报「无法定位到允许删除」",
      "无法定位到允许删除" not in str(data.get("text", "")), data.get("text"))

time.sleep(1.0)

# b) 索引里该 id 消失
tasks_after = []
if Path(TJ).exists():
    d = json.loads(Path(TJ).read_text(encoding="utf-8"))
    tasks_after = (d.get("tasks") if isinstance(d, dict) else d) or []
still = [t for t in tasks_after
         if isinstance(t, dict) and str(t.get("id")) == target]
chk("索引中该对话已消失", not still, "仍存在 %d 条" % len(still))
chk("索引条目数减少了", len(tasks_after) == len(tasks_before) - 1,
    "%d -> %d" % (len(tasks_before), len(tasks_after)))

# c) 历史上下文 JSON 被删
from _files_listing_fix import _conv_id_variants
gone = True
detail = []
for v in _conv_id_variants(target):
    for nm in (v + ".json", "conv_" + v + ".json"):
        p = Path(CDIR) / nm
        if p.exists():
            gone = False
            detail.append(nm)
chk("对应历史上下文 JSON 已删", gone, "残留: %s" % detail)

# d) 产物文件必须原封不动（最关键）
after = snapshot_products()
out.append("删除后产物: %d 个文件, 总 %d 字节" % (len(after), sum(s for _, s in after)))
chk("产物文件数量没变", len(after) == len(before),
    "%d -> %d" % (len(before), len(after)))
chk("产物总字节没变", sum(s for _, s in after) == sum(s for _, s in before),
    "%d -> %d" % (sum(s for _, s in before), sum(s for _, s in after)))
bset = set(p for p, _ in before)
aset = set(p for p, _ in after)
missing = bset - aset
chk("没有任何产物被误删", not missing, "丢失: %s" % list(missing)[:5])
# 逐个核对目标对话的产物
survived = all(Path(p).is_file() for p, _ in prod_of_target)
chk("该对话的产物全部存活", survived,
    "共 %d 个" % len(prod_of_target))

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
io.open(os.path.join(ROOT, "_delconv_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
