"""定向探针：验证 DeepSeek 收到系统提示词后是否会调用 docx_create 生成 Word。"""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

HOST, PORT = "127.0.0.1", 8888
OUT = Path(r"C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test")
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
    instr = ("请生成一份 Word 报告，保存到 C:\\Users\\X.LAPTOP-CA1GJQE3\\Desktop\\test\\report.docx，"
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
