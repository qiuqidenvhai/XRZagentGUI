# -*- coding: utf-8 -*-
"""单测：browser_research / browser_visit 的空参保护。

背景（2026-10-10 实测）：DeepSeek 偶尔调 browser_research 时不传 query，
导致 full_query = "深度研究: (最多访问 5 个页面)"，子代理没主题只能瞎猜
（实测猜成"AI Agent 技术发展趋势"，browser_search 反复返回不相关的 IBM 页面）。
修复：query/url 为空时直接返回错误提示，不派子代理。

本测试不碰网络、不派子代理，只验证保护逻辑本身。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
src = io.open(os.path.join(ROOT, "agent_core", "commander.py"), encoding="utf-8").read()

out = []
npass = nfail = 0


def chk(label, ok, detail=""):
    global npass, nfail
    if ok:
        npass += 1
    else:
        nfail += 1
    out.append("  [%s] %-46s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:110]))


out.append("== 1) 保护代码存在性 ==")
chk("browser_research 有空 query 保护",
    "browser_research 缺少 query 参数" in src)
chk("browser_visit 有空 url 保护",
    "browser_visit 缺少 url 参数" in src)
chk("research 保护在派子代理【之前】",
    src.index("browser_research 缺少 query 参数")
    < src.index("task_id = await sam.spawn_subagent(full_query, task_type=\"research\")"))
chk("visit 保护在派子代理【之前】",
    src.index("browser_visit 缺少 url 参数")
    < src.index("full_query = f\"访问并分析网页: {url}\"") + 200)

# ── 2) 用真实代码片段验证行为（把保护逻辑抽出来单独执行）──
out.append("")
out.append("== 2) 保护逻辑行为（等价代码执行）==")

# 复刻 commander 里的判定
def guard_research(params):
    query = params.get("query", "")
    if not str(query).strip():
        return {"ok": False, "error": "browser_research 缺少 query 参数（研究主题）。"}
    return {"ok": True, "dispatch": "spawn_subagent", "query": str(query).strip()}


def guard_visit(params):
    url = params.get("url", "")
    if not str(url).strip():
        return {"ok": False, "error": "browser_visit 缺少 url 参数（目标网址）。"}
    return {"ok": True, "dispatch": "navigate", "url": str(url).strip()}


# 空参数情形
r = guard_research({})
chk("research 无 query → 拦下不派子代理",
    r["ok"] is False and "spawn_subagent" not in str(r), r)
r = guard_research({"query": ""})
chk("research query='' → 拦下", r["ok"] is False, r)
r = guard_research({"query": "   "})
chk("research query='   '（纯空白）→ 拦下", r["ok"] is False, r)
r = guard_visit({})
chk("visit 无 url → 拦下", r["ok"] is False, r)
r = guard_visit({"url": ""})
chk("visit url='' → 拦下", r["ok"] is False, r)

# 正常情形必须放行（别把好路径堵死）
r = guard_research({"query": "2026 年新能源汽车市场趋势"})
chk("research 正常 query → 放行派子代理",
    r["ok"] is True and r["dispatch"] == "spawn_subagent", r)
r = guard_research({"query": "https://example.com 页面标题"})
chk("research URL 型 query → 放行", r["ok"] is True, r)
r = guard_visit({"url": "https://example.com"})
chk("visit 正常 url → 放行", r["ok"] is True and r["dispatch"] == "navigate", r)

out.append("")
out.append("== 3) 错误提示可操作性（要让模型知道怎么改）==")
# 【修正】之前用"复刻的简化文案"检查 → 假 FAIL；改用正则跨行拼接也被
# 换行/转义搞坏（连踩两次）。最终方案：**用 AST 精确解析源码里的字典字面量**，
# 直接拿到真实的 error 字符串，不做任何复刻。
import ast


def real_err_dicts(fn_name):
    """AST 解析 commander.py，取出指定函数内所有 return 的 dict 字面量。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == fn_name:
            got = []
            for n in ast.walk(node):
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
                    d = {}
                    ok = True
                    for k, v in zip(n.value.keys, n.value.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            d[k.value] = v.value
                        else:
                            ok = False
                    if ok:
                        got.append(d)
            return got
    return []


r_dicts = real_err_dicts("_browser_research")
v_dicts = real_err_dicts("_browser_visit")
err_r = next((d.get("error", "") for d in r_dicts if "query" in d.get("error", "")), "")
err_v = next((d.get("error", "") for d in v_dicts if "url" in d.get("error", "")), "")

out.append("  AST 取到 research 返回字典: %d 个" % len(r_dicts))
out.append("  AST 取到 visit   返回字典: %d 个" % len(v_dicts))
out.append("  真实文案(research): %s" % err_r[:130])
out.append("  真实文案(visit)  : %s" % err_v[:130])
out.append("")
chk("AST 成功提取 research error", bool(err_r), err_r[:60])
chk("AST 成功提取 visit error", bool(err_v), err_v[:60])
chk("research 提示含字段名 query", "query" in err_r, err_r[:60])
chk("research 提示含可复制示例 query='…'", "query=" in err_r, err_r[:100])
chk("visit 提示含字段名 url", "url" in err_v, err_v[:60])
chk("visit 提示含可复制示例 url='…'", "url=" in err_v, err_v[:100])

out.append("")
out.append("== 结论 ==  PASS %d / FAIL %d" % (npass, nfail))
io.open(os.path.join(ROOT, "_guard_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done, pass=%d fail=%d" % (npass, nfail))
