# -*- coding: utf-8 -*-
import re, html as _h
dom = open(r"D:/软件/XianRenZhangAgent/_deepseek_dom.html", encoding="utf-8").read()
out = []
out.append("DOM len=%d" % len(dom))
out.append("url contains chat.deepseek.com: %s" % ("chat.deepseek.com" in dom))


def cnt(pat, label, flags=0):
    try:
        n = len(re.findall(pat, dom, flags))
        out.append("%-34s count=%d" % (label, n))
    except Exception as e:
        out.append("%-34s ERR %r" % (label, e))


out.append("--- platforms.json current deepseek selectors ---")
cnt(r"<textarea[^>]*>", "input_selector='textarea'  (textarea tag)")
cnt(r"type=[\"']submit[\"']", "send_selector=button[type='submit']")
cnt(r"class=[\"'][^\"']*message[^\"']*[\"']", "response_selector=[class*='message']")
cnt(r"contenteditable=[\"']true[\"']", "contenteditable=true")
cnt(r"aria-label=[\"']发送[\"']", "aria-label=发送")
cnt(r"placeholder=[\"']", "placeholder (any)")
cnt(r"<button[^>]*>", "<button> (any)")
cnt(r"composer", "composer (class/word)")
cnt(r"data-role", "data-role")
cnt(r"ds-message|ds-markdown|message-content", "ds-message/ds-markdown/message-content")

out.append("")
out.append("--- enumerate distinct input-ish elements ---")
for m in list(re.finditer(r"<(textarea|input|button|div|span|a)[^>]*>", dom, re.I))[:0]:
    pass
# show any textarea with context
for m in re.finditer(r"<textarea[^>]*>", dom, re.I):
    out.append("  TEXTAREA: " + m.group(0)[:160])
# show aria-label / title that mention 发送 or send
for m in re.finditer(r"<[^>]*aria-label=[\"']([^\"']{0,20})[\"'][^>]*>", dom, re.I):
    v = m.group(1)
    if any(k in v for k in ["发送", "Send", "submit"]):
        out.append("  ARIA(发送/Send): " + v)
# show button text that mentions 发送
btns = re.findall(r"<button[^>]*>(.{0,40}?)</button>", dom, re.I | re.S)
out.append("  sample button texts: %s" % [b.strip()[:16] for b in btns[:15]])
open(r"D:/软件/XianRenZhangAgent/_dom_check.out", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
