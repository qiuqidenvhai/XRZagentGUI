# -*- coding: utf-8 -*-
"""决定性验证：/new_conversation 是否真的开了一个「独立上下文窗口」。

这是用户最核心的抱怨：「为什么新建对话没有开一个独立的上下文窗口」。
之前的坑：接口回 ok、页面 URL 也变了，但后端 self._messages 没清，
下一轮仍把整个旧对话拼进 prompt，模型于是挑一条历史指令回
（实测：发「产物归属测试」→ AI 回「上下文测试C」）。

判定方法（不猜，只看事实）：
  T1 在第 1 个对话里发 A（要求只回 token-A），拿回 A 的回复
  T2 点新对话（POST /new_conversation）
  T3 在第 2 个对话里发 B（要求只回 token-B）
  ✅ 通过条件：回复是 B 的 token，且【不包含】A 的 token
  ✅ 另外校验：会话 JSON 里 B 那条记录的 messages 不含 A 的历史
"""
import json
import os
import re
import time
import glob
import urllib.request
import urllib.error
import sys

API = "http://127.0.0.1:8888"
RESULTS = []
CONV_DIR = "xrz_data/XianRenZhang_tasks/conversations"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + ("   | " + str(detail)[:260] if detail else ""))


def post(path, payload, timeout=300):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, timeout=120):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def snapshot_conv_files():
    """当前所有会话 json 的快照（path -> mtime,size）。"""
    out = {}
    for p in glob.glob(os.path.join(CONV_DIR, "conv_*.json")):
        try:
            st = os.stat(p)
            out[p] = (st.st_mtime, st.st_size)
        except Exception:
            pass
    return out


def read_msgs(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("messages") or []
    except Exception:
        return []


def send_and_wait(text, timeout=240):
    """发一条消息，等本轮真正结束，返回 (本轮会话文件, 该文件消息列表)。

    【踩坑】不能只看「文件 mtime 变了」就立刻读 —— 后端是分多次落盘的：
    用户消息先写、AI 回复后写，中间还有个「协议纠正」的重试轮。
    只等一次变化就返回，会读到只有一个 user、AI 回复还没写进去的半成品
    （T3 因此误报 reply=''）。这里改为：等到「出现 assistant 消息且文件
    连续两次大小不再变化」才算稳定。
    """
    before = snapshot_conv_files()
    post("/command", {"command": text}, timeout=60)
    deadline = time.time() + timeout
    target = None
    last_size, stable, msgs = None, 0, []
    while time.time() < deadline:
        time.sleep(3)
        after = snapshot_conv_files()
        changed = [p for p, v in after.items() if before.get(p) != v]
        if not changed:
            continue
        target = max(changed, key=lambda p: after[p][0])
        msgs = read_msgs(target)
        has_ai = any(m.get("role") == "assistant" for m in msgs)
        cur_size = after[target][1]
        if has_ai and cur_size == last_size:
            stable += 1
            if stable >= 2:          # 连续两次没变化 → 写盘稳定
                break
        else:
            stable = 0
        last_size = cur_size
    return target, msgs


def wait_platform_idle(timeout=240):
    """等平台侧彻底空闲（没有正在生成的回复），否则上一轮没跑完就发新消息，
    新消息会排在上一条后面，读回来的 reply 是上一轮的（实测踩坑：
    本脚本误把上一次测试残留的 __UX_DRIVE__ 当成自己发的 A 的回复）。
    """
    end = time.time() + timeout
    idle_streak = 0
    while time.time() < end:
        try:
            d = get("/dom")
        except Exception:
            time.sleep(3)
            continue
        html = (d.get("html") or "").lower()
        # 生成中的信号：loading / generating / 思考中 / 正在生成
        busy = any(k in html for k in (
            "class=\"loading", "'loading'", "generating", "正在生成", "生成中"))
        if not busy:
            idle_streak += 1
            if idle_streak >= 3:      # 连续 3 次空闲才认为真的停了
                return True
        else:
            idle_streak = 0
        time.sleep(4)
    return False


def main():
    TOKEN_A = "ZZQA17"
    TOKEN_B = "QQZB42"

    h = get("/health")
    print("[环境] agent_ready=%s platform=%s" % (h.get("agent_ready"), h.get("platform")))

    # ── 0. 先强制开一个干净对话，并等平台空闲，避免上一轮残留污染 ──
    print("\n[0] 先 POST /new_conversation 清场，并等待平台空闲")
    r = post("/new_conversation", {}, timeout=180)
    print("    返回:", json.dumps(r, ensure_ascii=False)[:220])
    idle = wait_platform_idle()
    print("    平台空闲:", idle)
    if not idle:
        print("    [警告] 平台仍在生成，结果可能受上一轮影响")

    # ── T1. 第 1 个对话里发 A ────────────────────────────────────
    print("\n[T1] 在对话① 发 A =", TOKEN_A)
    fA, msgsA = send_and_wait("请只回复这串字符，不要有任何其它文字：%s" % TOKEN_A)
    print("    会话文件:", os.path.basename(fA) if fA else None, " 消息数:", len(msgsA))
    a_reply = ""
    for m in reversed(msgsA):
        if m.get("role") == "assistant":
            a_reply = str(m.get("content") or "")
            break
    # 用「最后一次 assistant」更稳：本轮回复应包含 A token
    check("T1 对话① 真的回了 A token", TOKEN_A in a_reply, "reply=%r" % a_reply[:150])
    check("T1 对话① 的回复里没有 B token", TOKEN_B not in a_reply, "reply=%r" % a_reply[:150])

    # ── T2. 开新对话 ────────────────────────────────────────────
    print("\n[T2] POST /new_conversation")
    t0 = time.time()
    r = post("/new_conversation", {}, timeout=180)
    dt = time.time() - t0
    print("    返回:", json.dumps(r, ensure_ascii=False)[:260], " 耗时=%.1fs" % dt)
    check("T2 新对话接口返回成功", bool(r.get("type") in ("ok", "accepted")),
          "type=%s text=%s" % (r.get("type"), str(r.get("text"))[:120]))
    # 关键：返回文案必须体现「已清空后端上下文」，否则就是又只换了页面
    txt = str(r.get("text") or "")
    check("T2 返回文案确认后端上下文已清空", ("清空" in txt) or ("上下文" in txt),
          "text=%r" % txt[:180])

    # ── T3. 第 2 个对话里发 B ────────────────────────────────────
    print("\n[T3] 在对话② 发 B =", TOKEN_B)
    idle2 = wait_platform_idle()
    print("    平台空闲:", idle2)
    fB, msgsB = send_and_wait("请只回复这串字符，不要有任何其它文字：%s" % TOKEN_B)
    print("    会话文件:", os.path.basename(fB) if fB else None, " 消息数:", len(msgsB))
    b_reply = ""
    for m in reversed(msgsB):
        if m.get("role") == "assistant":
            b_reply = str(m.get("content") or "")
            break
    check("T3 对话② 真的回了 B token", TOKEN_B in b_reply, "reply=%r" % b_reply[:150])
    # ★ 核心断言：新对话的回复绝不能带上一轮的 A token
    check("T3 【核心】对话② 的回复不串到 A（上下文真的独立）",
          TOKEN_A not in b_reply, "reply=%r" % b_reply[:200])

    # ── T4. 会话文件层面：新对话的 messages 不含上一轮历史 ────────
    user_msgs = [str(m.get("content") or "") for m in msgsB if m.get("role") == "user"]
    has_a_hist = any(TOKEN_A in u for u in user_msgs)
    check("T4 【核心】新对话的会话记录里不含上一轮的 A 历史",
          not has_a_hist, "user 消息=%s" % [u[:60] for u in user_msgs][:5])
    check("T4 新对话是独立文件(未写回对话①的文件)", fA != fB,
          "A=%s B=%s" % (os.path.basename(fA or ""), os.path.basename(fB or "")))

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n================ 结果 %d/%d ================" % (passed, total))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s | %s" % (name, str(detail)[:220]))
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
