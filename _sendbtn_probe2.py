# -*- coding: utf-8 -*-
import re
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom_after.html", encoding="utf-8").read()
out = []

out.append("=== all ds-button elements (opening tags) ===")
for k, m in enumerate(re.finditer(r"<button\b[^>]*class=[\"'][^\"']*ds-button[^\"']*[\"'][^>]*>", dom, re.I)):
    out.append("dsbutton[%d]: %s" % (k, re.sub(r"\s+", " ", m.group(0))[:160]))

out.append("")
out.append("=== stop / generating / loading indicators present? ===")
for tok in ["ds-stop", "generating", ".loading", "loading", "stop", "pause", "thinking"]:
    out.append("%-14s count=%d" % (tok, len(re.findall(re.escape(tok), dom, re.I))))

out.append("")
out.append("=== ds-think / collapsible (thinking) present? ===")
for tok in ["ds-think-content", "ds-collapsible-text", "深度思考", "联网搜索", "深度"]:
    out.append("%-16s count=%d" % (tok, len(re.findall(re.escape(tok), dom))))

out.append("")
out.append("=== composer toolbar toggle labels ===")
for m in re.finditer(r"<span class=\"_[0-9a-z]+\">([^<]{1,16})</span>", dom):
    t = m.group(1).strip()
    if t and len(t) < 16:
        out.append("  label: " + t)

open(r"D:/软件/XianRenZhangAgent/_sendbtn2.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
