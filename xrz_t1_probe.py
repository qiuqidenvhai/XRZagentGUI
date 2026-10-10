"""定向探针：验证 DeepSeek 收到系统提示词后是否会调用 docx_create 生成 Word。"""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

HOST, PORT = "127.0.0.1", 8888
# 【2026-10-06 修"写死桌面位置"】原来写死开发者桌面路径，换台电脑就跑到
# 别人桌面上去、还可能目录不存在。改为按本机真实环境解析。
try:
    from agent_core.user_paths import desktop_dir as _up_desktop
    OUT = Path(_up_desktop(create=True)) / "test"
except Exception:
    import os as _os
    OUT = Path(_os.path.expanduser("~")) / "Desktop" / "test"

REPORT = OUT / "report.docx"


def _post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def switch(p):
    return _post("/platform", {"platform": p})


def command(text):
    return _post("/command", {"command": text})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if REPORT.exists():
        REPORT.unlink()
    print("switch deepseek:", switch("deepseek").get("type"))
    time.sleep(1)
    print("new chat:", command("新对话").get("type"))
    time.sleep(1)
    # 【2026-10-06】提示词里的路径用本机真实测试目录，不写死开发者用户名
    _p = str(OUT / "report.docx").replace("\\", "\\\\")
    instr = (f"请生成一份 Word 报告，保存到 {_p}，"
             "标题为《仙人掌 Agent 自测报告》，下面包含3个小节：一、功能概览；二、测试结论；三、下一步计划。")
    print("post:", command(instr).get("type"))
    # 轮询产物
    for i in range(150):
        if REPORT.exists() and REPORT.stat().st_size >= 1000:
            print(f"REPORT_OK at {i}s size={REPORT.stat().st_size}")
            return
        time.sleep(1)
    print("REPORT_NOT_CREATED after 150s")
    # 落盘最新对话看模型是否回了 @@@@
    print("（未生成，详见后端日志 / conv JSON）")


if __name__ == "__main__":
    main()
