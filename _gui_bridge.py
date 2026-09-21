"""
9333 测试桥客户端：把 JS 跑进【真实 Qt GUI 窗口的页面上下文】里执行。
这就是“从 GUI 窗口发送”的忠实路径 —— 调用的是 gui.html 里
switchPlatform()/sendInput()/onModelChange() 这些“用户点击才会触发”的函数，
而不是绕过窗口直接打 8888。

用法：
  python _gui_bridge.py eval "<js 表达式或语句>"
  python _gui_bridge.py readlast            # 读取 msgDiv 最近几条消息
  python _gui_bridge.py shot <out.png>      # 截取真实窗口画面
  python _gui_bridge.py send "<文本>"       # 走 sendInput(forceText) 发送
  python _gui_bridge.py platform <name>     # 走 switchPlatform(name) 切换平台
  python _gui_bridge.py model <m>           # 走 onModelChange(m)
  python _gui_bridge.py health              # 读 /health 的 agent_ready + 当前平台
"""
import json
import sys
import urllib.request


def rpc(js, timeout=35):
    """POST 到 9333 测试桥，让 GUI 窗口内的页面执行 js，返回其 value。"""
    body = json.dumps({"kind": "eval", "js": js}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:9333/",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _val(js, timeout=35):
    d = rpc(js, timeout=timeout)
    return d.get("value")


def read_last(n=6):
    js = (
        "(function(){var m=document.getElementById('msgDiv');"
        "if(!m)return 'NO msgDiv';var els=m.querySelectorAll('.msg');"
        "var out=[];for(var i=Math.max(0,els.length-%d);i<els.length;i++){"
        "var e=els[i];out.push((e.className.replace('msg ','').slice(0,3))+': '+e.innerText.slice(0,300));}"
        "return JSON.stringify(out);})()" % n
    )
    v = _val(js)
    try:
        return json.loads(v) if isinstance(v, str) else v
    except Exception:
        return v


def cmd(name, arg=""):
    if name == "eval":
        print(_val(arg))
    elif name == "readlast":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        for line in read_last(n):
            print(line)
    elif name == "send":
        # 走 gui.html 的 sendInput(forceText)：探活→/command→accepted；回复经 SSE 进面板
        v = _val("sendInput(%s)" % json.dumps(arg), timeout=35)
        print("sendInput accepted-ish:", v)
        print("=== 面板最近消息 ===")
        for line in read_last(4):
            print(line)
    elif name == "platform":
        v = _val("switchPlatform(%s)" % json.dumps(arg), timeout=130)
        print("switchPlatform:", v)
    elif name == "model":
        print("onModelChange:", _val("onModelChange(%s)" % json.dumps(arg), timeout=35))
    elif name == "health":
        print("health(agent_ready,platform):",
              _val("fetch('/health').then(r=>r.json()).then(j=>JSON.stringify({agent_ready:j.agent_ready,platform:j.platform}))"))
    elif name == "shot":
        out = sys.argv[2]
        # kind=shot 走的是 view.grab() 截图；用独立请求
        body = json.dumps({"kind": "shot", "path": out}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:9333/", data=body,
                                    headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=35) as r:
            print("shot ->", out, json.loads(r.read().decode("utf-8")))
    else:
        print("unknown cmd", name)


if __name__ == "__main__":
    cmd(sys.argv[1] if len(sys.argv) > 1 else "health",
        sys.argv[2] if len(sys.argv) > 2 else "")
