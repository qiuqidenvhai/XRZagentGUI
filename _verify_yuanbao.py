# -*- coding: utf-8 -*-
"""切到 yuanbao（带重试，绕过偶发 ERR_CONNECTION_CLOSED），再验证新建对话开的是 yuanbao 自己的标签页。"""
import json
import urllib.request
import urllib.error
import sys
import time
import re

API = "http://127.0.0.1:8888"


def post(path, payload, timeout=200):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=30):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def dom_url():
    try:
        return (get("/dom", timeout=30) or {}).get("url") or ""
    except Exception as e:
        return "<err:%s>" % e


def parse_tabs(text):
    m = re.search(r"标签页\s*(\d+)\s*→\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


# 1) 重试切到 yuanbao
ready = False
for attempt in range(8):
    try:
        rp = post("/platform", {"platform": "yuanbao"}, timeout=90)
        print("  /platform 尝试%d: %s" % (attempt + 1, json.dumps(rp, ensure_ascii=False)[:140]))
    except Exception as e:
        print("  /platform 尝试%d 异常: %s" % (attempt + 1, e))
    for _ in range(12):
        try:
            hh = get("/health", timeout=5)
            if hh.get("agent_ready") and hh.get("platform") == "yuanbao":
                ready = True
                break
        except Exception:
            pass
        time.sleep(5)
    if ready:
        break
    time.sleep(3)

print("yuanbao 就绪:", ready)
if not ready:
    print("仍无法切到 yuanbao（疑似网络/代理偶发问题），结束。")
    sys.exit(2)

# 2) 验证新建对话在 yuanbao 上下文开新标签页
print("\n[yuanbao] /new_conversation x2")
allok = True
for k in (1, 2):
    before = dom_url()
    r = post("/new_conversation", {}, timeout=200)
    text = str(r.get("text") or "")
    after = dom_url()
    o, n = parse_tabs(text)
    ok_tab = (o is not None and n is not None and n > o)
    ok_host = ("yuanbao.tencent.com" in after)
    print("  第%d次: 标签页=%s 新标签页=%s 新页主机正确=%s 调前=%s 调后=%s" % (
        k, (o, n), ok_tab, ok_host, before[:50], after[:60]))
    allok = allok and ok_tab and ok_host

print("\nyuanbao 验证:", "PASS ✅" if allok else "FAIL ❌")
sys.exit(0 if allok else 1)
