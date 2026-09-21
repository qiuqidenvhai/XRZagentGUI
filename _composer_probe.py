# -*- coding: utf-8 -*-
import re
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom.html", encoding="utf-8").read()
out = []

# find the textarea and print surrounding ~1200 chars to see the composer container
i = dom.lower().find("<textarea")
if i >= 0:
    seg = dom[max(0, i - 400): i + 1200]
    out.append("=== textarea neighborhood ===")
    out.append(seg)

out.append("")
out.append("=== all <button> elements (attrs + short inner) ===")
for m in re.finditer(r"<button\b[^>]*>(.*?)</button>", dom, re.I | re.S):
    attrs = m.group(0)
    inner = re.sub(r"\s+", " ", m.group(1)).strip()[:40]
    out.append("BTN: " + attrs[:120] + " | inner=" + inner)

out.append("")
out.append("=== class names containing 'send' or 'submit' or 'action' or 'composer' ===")
for m in set(re.findall(r"class=[\"']([^\"']*(?:send|submit|action|composer|attach|mode)[^\"']*)[\"']", dom, re.I)):
    out.append("  " + m[:120])

open(r"D:/软件/XianRenZhangAgent/_composer.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
