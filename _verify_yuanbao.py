"""元宝专项验证（09-14）：验证 check_login 误报"未登录"修复是否生效。
切到元宝 → 发一条 chat（最轻）→ 发一条 file_write 工具调用。
看 final 是否真回答（而非「平台未登录」），tools 里是否有 file_write。
同时读后端日志里 元宝 的 check_login 结果。
"""
import sys, time, json, re
sys.path.insert(0, ".")
import _xrz_harness as H

ROOT = r"D:\软件\XianRenZhangAgent"


def log_grep():
    """抓后端日志里最近元宝的 check_login / 启动记录。"""
    import os
    p = os.path.join(ROOT, "_backend_v15.log")
    hits = []
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "元宝" in line and ("check_login" in line or "登录" in line or "启动" in line):
                    hits.append(line.rstrip())
    except Exception as e:
        hits.append(f"(读日志失败: {e})")
    return hits[-15:]


def main():
    print("===== 切到元宝 =====", flush=True)
    r = H.switch_platform("yuanbao", timeout=150)
    print(f"[switch] {r}", flush=True)

    print("\n===== 元宝 chat（最轻） =====", flush=True)
    c = H.run("你好，请只回答：1加1等于几？不要调用任何工具", timeout=150)
    print(f"[chat]  ok={c['ok']}  posted={c['posted']}", flush=True)
    print(f"[chat]  tools={sorted(c['tools'])}", flush=True)
    print(f"[chat]  errs={c['errs'][:3]}", flush=True)
    print(f"[chat]  final={c['final'][:300]!r}", flush=True)

    still_login_block = ("未登录" in (c["final"] or "")) or ("需要登录" in (c["final"] or ""))
    print(f"\n[判定] chat 仍报未登录？{still_login_block}", flush=True)

    print("\n===== 元宝 file_write 工具调用 =====", flush=True)
    import os
    txt = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "gui_session", "mp2_yuanbao_v15.txt")
    try:
        if os.path.exists(txt):
            os.replace(txt, txt + ".old")
    except Exception:
        pass
    f = H.run(f"请用 file_write 工具创建文件 mp2_yuanbao_v15.txt，内容为「yuanbao v15 修复验证成功」",
              timeout=300)
    print(f"[file_write]  ok={f['ok']}  posted={f['posted']}", flush=True)
    print(f"[file_write]  tools={sorted(f['tools'])}", flush=True)
    print(f"[file_write]  final={f['final'][:200]!r}", flush=True)
    ftools = "file_write" in f["tools"]
    print(f"[判定] file_write 工具真的被调用？{ftools}", flush=True)

    print("\n===== 后端日志 元宝 登录相关记录 =====", flush=True)
    for line in log_grep():
        print("  " + line, flush=True)

    print("\n========== 结论 ==========", flush=True)
    print(f"  chat 不再是未登录:  {not still_login_block}", flush=True)
    print(f"  file_write 工具可用: {ftools}", flush=True)


if __name__ == "__main__":
    main()
