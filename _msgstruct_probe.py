# -*- coding: utf-8 -*-
import re
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom_after.html", encoding="utf-8").read()
out = []

# Show each .ds-message element: its opening tag + first 200 chars of content
out.append("=== .ds-message elements ===")
for k, m in enumerate(re.finditer(r"<[a-zA-Z][^>]*class=[\"'][^\"']*ds-message[^\"']*[\"'][^>]*>", dom)):
    snippet = dom[m.start(): m.start() + 260]
    snippet = re.sub(r"\s+", " ", snippet)
    out.append("ds-message[%d]: %s" % (k, snippet[:240]))

out.append("")
out.append("=== .ds-markdown: how many, and is it inside ds-message? ===")
md = len(re.findall(r"ds-markdown", dom))
out.append("ds-markdown count=%d" % md)
# is ds-markdown nested inside a ds-message?
m1 = dom.find("ds-message")
m2 = dom.find("ds-markdown")
out.append("first ds-message idx=%d, first ds-markdown idx=%d (markdown after message? %s)" % (m1, m2, m2 > m1))

out.append("")
out.append("=== .ds-assistant-message-main-content ===")
for k, m in enumerate(re.finditer(r"<[a-zA-Z][^>]*class=[\"'][^\"']*ds-assistant-message-main-content[^\"']*[\"'][^>]*>", dom)):
    out.append("  assistant-content[%d]: %s" % (k, re.sub(r"\s+", " ", dom[m.start():m.start()+120])))

out.append("")
out.append("=== message-list / virtual-list containers ===")
for tok in ["ds-virtual-list", "message-list", "conversation", "chat-list"]:
    out.append("%-20s count=%d" % (tok, len(re.findall(tok, dom))))

open(r"D:/软件/XianRenZhangAgent/_msgstruct.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
