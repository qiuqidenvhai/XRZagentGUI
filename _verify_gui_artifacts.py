"""GUI 真机验收脚本：断言三个修复在真实窗口 DOM 里生效。

通过桌面壳测试桥（127.0.0.1:9333）在真实 QtWebEngine 窗口里跑 JS 断言。
桥返回格式：{"value": "<JS 返回值>"}，且 JS 端必须 JSON.stringify 再回传
（对象/数组会被退化成空串）。多行 JS 必须写成 (() => {...})()。
"""
import json
import os
import time
import urllib.request

BRIDGE = "http://127.0.0.1:9333/"

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(
    HERE, "xrz_data", ".xianrenzhang_agent", "conversation_artifacts.json"
)
TEST_CONV = "deepseek_TEST_SCOPE_001"
TEST_ARTIFACT = "mp2_deepseek.docx"


def seed_registry():
    """Register one artifact for TEST_CONV so step 3 has real data to read.

    The suite is meant to be runnable on a clean machine; earlier runs cleaned
    the registry, which made step 3 fail for lack of fixtures rather than a bug.
    """
    data = {}
    if os.path.exists(REGISTRY):
        try:
            data = json.loads(open(REGISTRY, encoding="utf-8").read()) or {}
        except Exception:
            data = {}
    data[TEST_CONV] = [os.path.join(HERE, TEST_ARTIFACT)]
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def cleanup_registry():
    """Remove only our own fixture entries, leaving real user data untouched."""
    if not os.path.exists(REGISTRY):
        return
    try:
        data = json.loads(open(REGISTRY, encoding="utf-8").read()) or {}
    except Exception:
        return
    for k in list(data.keys()):
        if k.endswith("_TEST_SCOPE_001") or k.endswith("_OTHER_999"):
            data.pop(k, None)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _call(payload, timeout=60):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BRIDGE, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    try:
        outer = json.loads(raw)
    except Exception:
        return raw
    val = outer.get("value", outer) if isinstance(outer, dict) else outer
    # 内层可能是被 JSON.stringify 过的字符串，再解一层
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def ev(js, timeout=60):
    return _call({"js": js}, timeout)


def shot(path, timeout=90):
    return _call({"kind": "shot", "path": path}, timeout)


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name +
          (("  | " + str(detail)) if detail != "" else ""))


def main():
    print("=== 0) 播种测试数据 ===")
    seed_registry()
    print("   已为 %s 注册 %s" % (TEST_CONV, TEST_ARTIFACT))

    print()
    print("=== 1) 冷启动：没有任务 → 侧边栏必须为空 ===")
    d = ev("""(() => {
      const fl = document.getElementById('fileList');
      const items = fl ? fl.querySelectorAll('.file-item').length : -1;
      const empty = fl ? fl.querySelector('.file-empty') !== null : false;
      return JSON.stringify({
        items: items, empty: empty,
        scope: (typeof FILE_SCOPE_CONV !== 'undefined') ? String(FILE_SCOPE_CONV) : 'undef'
      });
    })()""")
    print("   parsed:", d)
    check("侧边栏 0 个产物", d.get("items") == 0, d.get("items"))
    check("显示空态提示", d.get("empty") is True, d.get("empty"))
    check("FILE_SCOPE_CONV 为空", d.get("scope") in ("null", "", "undef"), d.get("scope"))

    print()
    print("=== 2) 前端函数已定义 ===")
    d = ev("""(() => JSON.stringify({
      bind: typeof bindFileScope,
      load: typeof loadAttachments,
      clear: typeof clearPendingAttachments,
      rec: typeof recordArtifactsFromTool
    }))()""")
    print("   parsed:", d)
    for k in ("bind", "load", "clear", "rec"):
        check("函数 %s" % k, d.get(k) == "function", d.get(k))

    print()
    print("=== 3) 绑定某任务 → 只显示该任务产物 ===")
    d = ev("""(() => {
      bindFileScope('deepseek_TEST_SCOPE_001');
      return JSON.stringify({scope: String(FILE_SCOPE_CONV)});
    })()""")
    check("已绑定测试任务", d.get("scope") == "deepseek_TEST_SCOPE_001", d.get("scope"))

    time.sleep(3)
    d = ev("""(() => {
      const fl = document.getElementById('fileList');
      const items = fl ? Array.from(fl.querySelectorAll('.file-item .file-name')).map(e=>e.textContent) : [];
      return JSON.stringify({items: items});
    })()""")
    print("   parsed:", d)
    items = (d or {}).get("items") or []
    check("该任务显示 1 个产物", len(items) == 1, items)
    check("产物是 mp2_deepseek.docx",
          any("mp2_deepseek.docx" in str(x) for x in items), items)

    print()
    print("=== 4) 绑定无产物的任务 → 必须为空（不串场）===")
    ev("""(() => { bindFileScope('deepseek_OTHER_999'); return JSON.stringify({ok:1}); })()""")
    time.sleep(2)
    d = ev("""(() => {
      const fl = document.getElementById('fileList');
      return JSON.stringify({items: fl ? fl.querySelectorAll('.file-item').length : -1});
    })()""")
    print("   parsed:", d)
    check("无产物任务显示 0 个", (d or {}).get("items") == 0, (d or {}).get("items"))

    print()
    print("=== 5) 新建对话 → 侧边栏清空 ===")
    ev("""(() => { resetChatView(); return JSON.stringify({ok:1}); })()""")
    time.sleep(2)
    d = ev("""(() => {
      const fl = document.getElementById('fileList');
      return JSON.stringify({
        items: fl ? fl.querySelectorAll('.file-item').length : -1,
        scope: String(FILE_SCOPE_CONV)
      });
    })()""")
    print("   parsed:", d)
    check("新对话后 0 个产物", (d or {}).get("items") == 0, (d or {}).get("items"))
    check("scope 已清空", (d or {}).get("scope") in ("null", ""), (d or {}).get("scope"))

    print()
    print("=== 6) 截图留证 ===")
    try:
        print("   shot:", shot("D:/软件/XianRenZhangAgent/_verify_gui_artifacts.png"))
    except Exception as e:
        print("   shot err:", e)

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 62)
    print("RESULT: %d/%d PASS" % (passed, len(results)))
    for n, ok, det in results:
        if not ok:
            print("  FAILED:", n, "|", det)
    cleanup_registry()
    print("   fixtures cleaned")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
