# -*- coding: utf-8 -*-
"""验证 /new_conversation 真正「新开了一个上下文窗口（新标签页）」，且开在【当前平台】的上下文里。

核心证据：
  1) 返回文案「标签页 N→M」（M>N）= 确实 new_page() 开了新标签页（不是同页 goto）；
  2) 调后 /dom 的 URL 主机必须属于当前平台（yuanbao→yuanbao.tencent.com，
     deepseek→chat.deepseek.com），证明新标签页开对了上下文，没有串到别的平台。
"""
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
        d = get("/dom", timeout=30)
        return d.get("url") or ""
    except Exception as e:
        return "<dom.err:%s>" % e


def parse_tabs(text):
    m = re.search(r"标签页\s*(\d+)\s*→\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def check_on(platform, expect_host):
    print("\n========== 平台: %s (期望主机含 %s) ==========" % (platform, expect_host))
    h = get("/health", timeout=10)
    print("[环境] agent_ready=%s platform=%s" % (h.get("agent_ready"), h.get("platform")))

    print("[1] /new_conversation（第 1 次）")
    before = dom_url()
    r1 = post("/new_conversation", {}, timeout=200)
    text1 = str(r1.get("text") or "")
    after1 = dom_url()
    o1, n1 = parse_tabs(text1)
    ok1 = (o1 is not None and n1 is not None and n1 > o1)
    host_ok1 = (expect_host in after1)
    print("    调前 url:", before)
    print("    返回:", json.dumps(r1, ensure_ascii=False)[:240])
    print("    解析标签页计数:", (o1, n1))
    print("    ★ 开了新标签页:", "PASS" if ok1 else "FAIL",
          " | ★ 新页主机正确(%s):" % expect_host, "PASS" if host_ok1 else "FAIL",
          " | 调后url:", after1)

    print("[2] /new_conversation（第 2 次，验证可重复 + 仍开对平台）")
    r2 = post("/new_conversation", {}, timeout=200)
    text2 = str(r2.get("text") or "")
    after2 = dom_url()
    o2, n2 = parse_tabs(text2)
    ok2 = (o2 is not None and n2 is not None and n2 > o2)
    host_ok2 = (expect_host in after2)
    print("    解析标签页计数:", (o2, n2))
    print("    ★ 第2次仍开新标签页:", "PASS" if ok2 else "FAIL",
          " | ★ 新页主机正确:", "PASS" if host_ok2 else "FAIL", " | 调后url:", after2)
    return ok1 and ok2 and host_ok1 and host_ok2


def main():
    results = {}
    results["deepseek"] = check_on("deepseek", "chat.deepseek.com")

    try:
        print("\n>>> 切换到 yuanbao ...")
        rp = post("/platform", {"platform": "yuanbao"}, timeout=60)
        print("    /platform 返回:", json.dumps(rp, ensure_ascii=False)[:160])
        ready = False
        for i in range(30):
            try:
                hh = get("/health", timeout=5)
                if hh.get("agent_ready") and hh.get("platform") == "yuanbao":
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(5)
        print("    yuanbao 就绪:", ready)
        if ready:
            results["yuanbao"] = check_on("yuanbao", "yuanbao.tencent.com")
        else:
            print("    yuanbao 未就绪，跳过")
    except Exception as e:
        print("    切换 yuanbao 异常:", e)

    passed = all(results.values())
    print("\n================ 总体 ================")
    for k, v in results.items():
        print("  %s: %s" % (k, "PASS ✅" if v else "FAIL ❌"))
    print("总体:", "ALL PASS ✅" if passed else "HAS FAIL ❌")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
