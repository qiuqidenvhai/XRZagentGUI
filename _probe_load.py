"""后端忙时 /attachments 到底要多久？

背景：GUI 点历史任务时，右侧产物栏偶发长时间停在「⏳ 正在读取…」。
Python 侧空载实测 0.1s，所以怀疑「后端在驱动浏览器跑任务时 HTTP 变慢」。
这里复现：发一条真实任务，任务运行期间每秒打一次 /attachments 计时。
"""
import json
import threading
import time
import urllib.parse
import urllib.request

API = "http://127.0.0.1:8888"
TID = "deepseek_20260913_143112_10810"

STOP = False
SAMPLES = []


def poll_loop():
    while not STOP:
        t0 = time.time()
        try:
            r = urllib.request.urlopen(
                API + "/attachments?conversation_id=" + urllib.parse.quote(TID),
                timeout=60)
            n = len(json.loads(r.read().decode()).get("files") or [])
            SAMPLES.append((round(time.time() - t0, 2), n, ""))
        except Exception as e:
            SAMPLES.append((round(time.time() - t0, 2), None, str(e)[:60]))
        time.sleep(0.5)


def health():
    t0 = time.time()
    try:
        d = json.loads(urllib.request.urlopen(API + "/health", timeout=30).read().decode())
        return round(time.time() - t0, 2), d.get("agent_ready")
    except Exception as e:
        return round(time.time() - t0, 2), str(e)[:50]


def main():
    print("空载基线:", health(), "attachments:",
          urllib.request.urlopen(API + "/attachments?conversation_id=" + TID,
                                 timeout=30).status)

    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    body = json.dumps({"command": "巡检测试负载：请只回复 负载OK，不要调用任何工具"}).encode()
    req = urllib.request.Request(API + "/command", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=60)
        print("POST /command ->", r.status, r.read().decode()[:120],
              "(%.2fs)" % (time.time() - t0))
    except Exception as e:
        print("POST /command 失败:", e)

    print("负载期 25s 采样（每 0.5s 一次 /attachments）：")
    time.sleep(25)
    global STOP
    STOP = True
    time.sleep(1)

    ok = [s for s in SAMPLES if s[1] is not None]
    bad = [s for s in SAMPLES if s[1] is None]
    if ok:
        lat = sorted(s[0] for s in ok)
        print("  成功 %d 次 / 失败 %d 次" % (len(ok), len(bad)))
        print("  延迟 min=%.2fs 中位=%.2fs p90=%.2fs max=%.2fs"
              % (lat[0], lat[len(lat) // 2], lat[int(len(lat) * 0.9)], lat[-1]))
    for s in SAMPLES:
        if s[0] > 3 or s[1] is None:
            print("   慢/失败样本:", s)
    print("任务后 health:", health())


if __name__ == "__main__":
    main()
