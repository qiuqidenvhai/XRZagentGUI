#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史任务逐项体检：把 conversations/ 里每一条已做过的任务读出来，逐项判定健康度。

判定项（每一项都对应一个真实踩过的坑）：
  A 内部提示词污染   user 消息里出现 [SYSTEM] NO @@@@ PROTOCOL / [系统] 工具…执行结果
  B 空回复           assistant 正文为空 / 只有 （未收到回复）
  C 失控增长         消息数异常大（同一任务被反复重发叠加）
  D 状态行当回复     把「已经完成思考 / 正在思考 / 自动」等 UI 状态行当成 AI 回答
  E 乱码             出现 GBK 乱码（网页 shell 输出解码错误）
  F 空会话           完全没有消息
  G 回声污染         assistant 内容其实是 Agent 自己发出去的整段系统提示词
输出：history_audit_report.json + 控制台逐项清单
"""
import json, glob, os, re, sys, collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:\软件\XianRenZhangAgent"
CONV_DIR = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "conversations")

INTERNAL_MARKS = ["[SYSTEM] NO @@@@ PROTOCOL", "[系统] 工具 ", "[系统指令", "[系统纠正]",
                  "[系统] 用户要求继续", "[用户插话]", "[格式提醒"]
EMPTY_MARKS = ["（未收到回复）", "(未收到回复)", "未收到回复"]
STATUS_LINES = ["已经完成思考", "已完成思考", "思考完成", "正在思考", "思考中",
                "正在生成", "生成中", "自动", "Auto", "深度思考完成", "已深度思考"]
ECHO_MARKS = ["你是仙人掌 Agent（XianRenZhang Agent）", "=== 核心指令 ==="]
MOJIBAKE_RE = re.compile(r"[\u0400-\u04ff]{2,}|[�]{2,}")


def load(f):
    with open(f, encoding="utf-8") as fh:
        return json.load(fh)


def audit_one(f):
    """返回单条任务的体检结果"""
    name = os.path.basename(f)
    rec = {"file": name, "id": name[len("conv_"):-len(".json")], "issues": [],
           "n": 0, "n_user": 0, "n_ai": 0, "platform": "", "title": "",
           "mtime": os.path.getmtime(f)}
    try:
        d = load(f)
    except Exception as e:
        rec["issues"].append(f"F 会话文件无法解析: {e}")
        return rec
    msgs = d.get("messages") or []
    rec["platform"] = d.get("platform") or name.split("_")[1]
    rec["n"] = len(msgs)
    users = [m for m in msgs if m.get("role") == "user"]
    ais = [m for m in msgs if m.get("role") == "assistant"]
    rec["n_user"], rec["n_ai"] = len(users), len(ais)
    if users:
        rec["title"] = (users[0].get("content") or "")[:60].replace("\n", " ")

    # F 空会话
    if not msgs:
        rec["issues"].append("F 空会话：没有任何消息")
        return rec

    # A 内部提示词污染
    a_hits = [m["content"][:60] for m in users
              if any(k in (m.get("content") or "") for k in INTERNAL_MARKS)]
    if a_hits:
        rec["issues"].append(f"A 内部提示词被写进历史（{len(a_hits)} 条，例：{a_hits[0][:40]}）")

    # G 回声污染
    g_hits = [m for m in ais if any(k in (m.get("content") or "") for k in ECHO_MARKS)]
    if g_hits:
        ratio = len(g_hits) / max(1, len(ais))
        rec["issues"].append(f"G 回声污染：{len(g_hits)}/{len(ais)} 条 assistant "
                             f"内容其实是 Agent 自己发出去的提示词（{ratio:.0%}）")

    # B 空回复
    b_hits = [m for m in ais if any(k in (m.get("content") or "") for k in EMPTY_MARKS)]
    if b_hits:
        rec["issues"].append(f"B 空回复/未收到回复 ×{len(b_hits)}")

    # C 失控增长
    if len(msgs) >= 100:
        rec["issues"].append(f"C 失控增长：消息数 {len(msgs)}（同一任务被反复重发叠加）")

    # D 状态行当回复
    d_hits = [m for m in ais
              if (m.get("content") or "").strip() in STATUS_LINES
              or (m.get("content") or "").strip().split("\n")[0] in STATUS_LINES]
    if d_hits:
        rec["issues"].append(f"D 状态行被当成 AI 回复 ×{len(d_hits)}"
                             f"（例：{(d_hits[0].get('content') or '').strip()[:20]}）")

    # E 乱码
    if any(MOJIBAKE_RE.search(m.get("content") or "") for m in msgs):
        rec["issues"].append("E 出现乱码（网页 shell 输出/编码错误）")

    # N 没有 AI 回复
    if not ais:
        rec["issues"].append("B 没有 AI 回复")
    return rec


def main():
    files = sorted(glob.glob(os.path.join(CONV_DIR, "conv_*.json")))
    recs = [audit_one(f) for f in files]
    recs.sort(key=lambda r: -r["mtime"])

    by_plat = collections.Counter(r["platform"] for r in recs)
    healthy = [r for r in recs if not r["issues"]]
    broken = [r for r in recs if r["issues"]]
    kinds = collections.Counter()
    for r in broken:
        for i in r["issues"]:
            kinds[i[0]] += 1

    print("=" * 66)
    print(f"历史任务逐项体检：共 {len(recs)} 条")
    print("=" * 66)
    print("平台分布:", dict(by_plat))
    print(f"健康 {len(healthy)} 条 / 有问题 {len(broken)} 条")
    print("问题分类计数:",
          {"A 内部提示词污染": kinds["A"], "B 空回复": kinds["B"],
           "C 失控增长": kinds["C"], "D 状态行当回复": kinds["D"],
           "E 乱码": kinds["E"], "F 空会话": kinds["F"], "G 回声污染": kinds["G"]})
    print()
    print("── 有问题的任务逐项清单（时间倒序，前 40 条）──")
    for r in broken[:40]:
        tag = "  ".join(i.split(" ")[0] for i in r["issues"])
        print(f"  [{tag}] {r['id']}  n={r['n']} u={r['n_user']} a={r['n_ai']}  "
              f"| {r['title'][:34]}")
        for i in r["issues"]:
            print(f"        - {i}")

    out = os.path.join(ROOT, "history_audit_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"total": len(recs), "healthy": len(healthy), "broken": len(broken),
                   "by_platform": dict(by_plat), "issue_kinds": dict(kinds),
                   "items": recs}, fh, ensure_ascii=False, indent=2)
    print()
    print("报告已写入:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
