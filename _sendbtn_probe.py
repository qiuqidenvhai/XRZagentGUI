# -*- coding: utf-8 -*-
import re
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom_after.html", encoding="utf-8").read()
out = []

# Find the composer region: the textarea with placeholder "给 DeepSeek 发送消息"
i = dom.find("给 DeepSeek 发送消息")
out.append("composer anchor idx=%d" % i)
if i > 0:
    # grab a wide window AFTER the composer (send button is to the right / below)
    seg = dom[i:i + 4000]
    out.append("=== composer + send region (4000 chars after placeholder) ===")
    # strip svg noise for readability
    seg2 = re.sub(r"<svg.*?</svg>", " <svg…/> ", seg, flags=re.S)
    seg2 = re.sub(r"\s+", " ", seg2)
    out.append(seg2[:2500])

out.append("")
out.append("=== elements with 'send' in class or aria anywhere (whole doc) ===")
for m in re.finditer(r"[\"'][^\"']*send[^\"']*[\"']", dom, re.I):
    out.append("  " + m.group(0)[:80])

out.append("")
out.append("=== ds- class tokens (whole doc, unique) ===")
for m in sorted(set(re.findall(r"\bds-[a-z0-9-]+", dom, re.I))):
    out.append("  " + m)

open(r"D:/软件/XianRenZhangAgent/_sendbtn.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
