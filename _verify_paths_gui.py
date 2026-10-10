# -*- coding: utf-8 -*-
"""真机验证（GUI 窗口内 9333 桥驱动）：
 1) GUI 界面正常加载、平台列表拉到
 2) 后端在线
 3) 系统提示词里的示例路径 == 本机真实桌面（界面上体现为 AI 收到的指令正确）
"""
import io
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

out = []


def bridge(js, kind="eval", path="", timeout=40):
    body = {"kind": kind, "js": js}
    if kind == "shot":
        body["path"] = path
    req = urllib.request.Request("http://127.0.0.1:9333/", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(_op.open(req, timeout=timeout).read()).get("value")


# 1) 后端
try:
    st = _op.open("http://127.0.0.1:8888/health", timeout=6).status
    out.append("[1] 后端 /health: %s %s" % (st, "PASS" if st == 200 else "FAIL"))
except Exception as e:
    out.append("[1] 后端 DOWN: %r  FAIL" % e)

# 2) GUI 真实 DOM
try:
    r = bridge(r"""(() => {
      const t = (document.querySelector('.chat-title,#title,.title')||{}).textContent || '';
      const plats = Array.from(document.querySelectorAll('.platform-item,.plat-item,[data-plat]')).map(e=>e.textContent.trim()).filter(Boolean);
      const msgs = document.getElementById('messages');
      return JSON.stringify({
        title: t.trim().slice(0,40),
        plat_count: plats.length,
        plats: plats.slice(0,6),
        messages_ok: !!msgs && msgs.children.length > 0,
        body_len: document.body.innerText.length
      });
    })()""")
    d = json.loads(r or "{}")
    out.append("[2] GUI DOM: %s" % json.dumps(d, ensure_ascii=False))
    out.append("    界面有内容: %s" % ("PASS" if d.get("body_len", 0) > 50 else "FAIL"))
    out.append("    平台列表: %s %s" % (d.get("plat_count"),
                                "PASS" if d.get("plat_count", 0) > 0 else "FAIL"))
except Exception as e:
    out.append("[2] GUI 桥失败: %r" % e)

# 3) 提示词路径（后端真实模块）
try:
    from agent_core.commander import Commander
    from agent_core.user_paths import desktop_dir, json_path

    c = Commander.__new__(Commander)

    class _R:
        def __init__(self):
            self._tools = {}

        def list_tools(self):
            return []

    c._tools = _R()
    c._session = None
    c._running = False
    c._work_dir = None
    p = c._build_system_prompt()
    real = desktop_dir()
    out.append("[3] 本机真实桌面: %s" % real)
    out.append("[3] 提示词含本机桌面(转义后): %s"
               % ("PASS" if json_path(real)[1:-1] in p else "FAIL"))
    out.append("[3] 提示词含开发者路径写法 D:\\\\软件\\\\: %s"
               % ("FAIL(不该有)" if "D:\\\\软件" in p else "PASS"))
    # 提示词里的 JSON 示例必须可解析
    import re
    m = re.search(r'"path":"(.+?)\\\\test\\\\report\.docx"', p)
    if m:
        try:
            got = json.loads('{"path":"%s"}' % m.group(1))["path"]
            ok = got == os.path.join(real, "test", "report.docx")
            out.append("[3] 提示词示例 JSON 可解析且路径正确: %s (%s)"
                       % ("PASS" if ok else "FAIL", got))
        except Exception as e:
            out.append("[3] 提示词示例 JSON 解析失败: %r  FAIL" % e)
    else:
        out.append("[3] 未匹配到示例路径 FAIL")
except Exception:
    import traceback
    out.append("[3] FAIL:\n" + traceback.format_exc())

# 4) 截图留证
try:
    shot = os.path.join(ROOT, "xrz_data", "XianRenZhang_tasks", "_verify_paths_gui.png")
    bridge("", kind="shot", path=shot)
    out.append("[4] 截图: %s (存在=%s)" % (shot, os.path.isfile(shot)))
except Exception as e:
    out.append("[4] 截图失败: %r" % e)

io.open(os.path.join(ROOT, "_verify_paths_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done")
