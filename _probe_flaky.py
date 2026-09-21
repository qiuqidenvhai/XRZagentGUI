"""追查 history_task_products 偶发失败：同一 id 反复问 /attachments，看是否稳定。"""
import json
import time
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8888"
TID = "deepseek_20260913_143112_10810"


def ask(tid, timeout=30):
    t0 = time.time()
    try:
        r = urllib.request.urlopen(
            API + "/attachments?conversation_id=" + urllib.parse.quote(tid),
            timeout=timeout)
        d = json.loads(r.read().decode())
        return len(d.get("files") or []), "%.2fs" % (time.time() - t0), d.get("conversation_id")
    except Exception as e:
        return None, "%.2fs" % (time.time() - t0), str(e)


def main():
    print("== 连问 8 次同一 id ==")
    for i in range(8):
        n, dt, cid = ask(TID)
        print("  #%d files=%s %s cid=%r" % (i, n, dt, cid))
        time.sleep(1)
    print("\n== 带尾巴 / 不带尾巴 / 大写 ==")
    for tid in (TID, TID.rsplit("_", 1)[0], TID.upper()):
        n, dt, cid = ask(tid)
        print("  %s -> %s (%s)" % (tid, n, dt))
    print("\n== 后端 health ==")
    print(" ", urllib.request.urlopen(API + "/health", timeout=10).read().decode())


if __name__ == "__main__":
    main()
