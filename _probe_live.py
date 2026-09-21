"""实时探测：切到 通义/豆包/元宝，各发一条最轻的消息，抓当前真实返回。
目的：不新开浏览器，仅用现有后端，确认这三家【现在】到底是 已登录/额度耗尽/未登录。
"""
import sys, time, json
sys.path.insert(0, ".")
import _xrz_harness as H


def probe(key, label):
    print(f"\n########## 切到 {label} ({key}) ##########", flush=True)
    try:
        r = H.switch_platform(key, timeout=150)
        print(f"[switch] {r}", flush=True)
    except Exception as e:
        print(f"[switch] 异常: {e}", flush=True)
    try:
        c = H.run("你好，请只回答：1加1等于几？", timeout=120)
        print(f"[chat]  ok={c['ok']}  posted={c['posted']}", flush=True)
        print(f"[chat]  tools={sorted(c['tools'])}", flush=True)
        print(f"[chat]  errs={c['errs'][:3]}", flush=True)
        print(f"[chat]  final={c['final'][:400]!r}", flush=True)
    except Exception as e:
        print(f"[chat] 异常: {e}", flush=True)
    return c


if __name__ == "__main__":
    out = {}
    for k, l in (("tongyi", "通义千问"), ("doubao", "豆包"), ("yuanbao", "元宝")):
        try:
            c = probe(k, l)
            out[k] = c["final"][:400] if c else None
        except Exception as e:
            out[k] = f"EXC {e}"
    print("\n\n========== 汇总 ==========")
    print(json.dumps(out, ensure_ascii=False, indent=2))
