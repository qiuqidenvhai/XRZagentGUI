# -*- coding: utf-8 -*-
import re
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom_after.html", encoding="utf-8").read()
out = []
out.append("DOM len=%d" % len(dom))


def cnt(pat, label, flags=0):
    try:
        out.append("%-40s count=%d" % (label, len(re.findall(pat, dom, flags))))
    except Exception as e:
        out.append("%-40s ERR %r" % (label, e))


out.append("--- candidate send-button / reply containers (hydrated) ---")
cnt(r"<button\b", "<button>")
cnt(r"aria-label=[\"'][^\"']*send", "aria-label*=send")
cnt(r"aria-label=[\"']发送[\"']", "aria-label=发送")
cnt(r"aria-label=[\"'][^\"']*submit", "aria-label*=submit")
cnt(r"type=[\"']submit[\"']", "type=submit")
cnt(r"class=[\"'][^\"']*(?:message|chat-msg|conversation|msg-bubble|markdown)[^\"']*[\"']", "class*=(message|msg|markdown|conversation)")
cnt(r"ds-message", "ds-message")
cnt(r"markdown", "markdown")
cnt(r"data-slot=", "data-slot=")
cnt(r"role=[\"']log[\"']", "role=log")

out.append("")
out.append("--- all aria-label values present ---")
for m in sorted(set(re.findall(r"aria-label=[\"']([^\"']{1,24})[\"']", dom))):
    out.append("  aria-label: " + m)

out.append("")
out.append("--- buttons with their aria-label / title / text ---")
for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", dom, re.I | re.S):
    attrs = m.group(1)
    inner = re.sub(r"\s+", " ", m.group(2)).strip()[:30]
    al = re.search(r"aria-label=[\"']([^\"']{1,24})[\"']", attrs)
    tt = re.search(r"title=[\"']([^\"']{1,24})[\"']", attrs)
    tag = "aria=" + al.group(1) if al else ("title=" + tt.group(1) if tt else "")
    out.append("  BTN [%s] inner=%r" % (tag, inner))

open(r"D:/软件/XianRenZhangAgent/_after_check.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
