"""GUI 真机验收：DeepSeek 模型/搜索控件是否符合真实页面。

断言：
  - 平台切到 DeepSeek 时，「模型」下拉整个隐藏（网页没有模型选择器）
  - 「搜索」控件显示（网页确有「智能搜索」开关）
  - 切到别的平台（豆包）时，模型下拉恢复显示且选项来自配置
"""
import json
import time
import urllib.request

BRIDGE = "http://127.0.0.1:9333/"
results = []


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
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def ev(js, timeout=60):
    return _call({"js": js}, timeout)


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name +
          (("  | " + str(detail)) if detail != "" else ""))


def main():
    print("=== A) DeepSeek：模型下拉应隐藏，搜索控件应显示 ===")
    # 等平台列表加载完
    for _ in range(20):
        d = ev("""(() => JSON.stringify({
          n: Object.keys(PLATFORMS || {}).length,
          cur: CURRENT_PLATFORM || null
        }))()""")
        if isinstance(d, dict) and d.get("n"):
            break
        time.sleep(1)
    print("   PLATFORMS:", d)

    d = ev("""(() => {
      const ds = (PLATFORMS || {}).deepseek;
      if (!ds) return JSON.stringify({err:'no deepseek in PLATFORMS'});
      // 切到 deepseek 并渲染控件
      CURRENT_PLATFORM_CFG = ds;
      renderModelSelect(ds);
      renderSearchControl(ds);
      const mg = document.getElementById('modelGroup');
      const sg = document.getElementById('searchGroup');
      return JSON.stringify({
        models: (ds.models || []).length,
        has_search: !!ds.has_search,
        modelGroupDisplay: mg ? (mg.style.display || 'visible') : 'missing',
        searchGroupDisplay: sg ? (sg.style.display || 'visible') : 'missing'
      });
    })()""")
    print("   parsed:", d)
    check("DeepSeek models 为空", (d or {}).get("models") == 0, (d or {}).get("models"))
    check("DeepSeek 模型控件已隐藏",
          (d or {}).get("modelGroupDisplay") == "none",
          (d or {}).get("modelGroupDisplay"))
    check("DeepSeek 搜索控件可见",
          (d or {}).get("searchGroupDisplay") != "none",
          (d or {}).get("searchGroupDisplay"))

    print()
    print("=== B) 豆包：模型下拉应显示且选项来自配置 ===")
    d = ev("""(() => {
      const db = (PLATFORMS || {}).doubao;
      if (!db) return JSON.stringify({err:'no doubao'});
      CURRENT_PLATFORM_CFG = db;
      renderModelSelect(db);
      renderSearchControl(db);
      const mg = document.getElementById('modelGroup');
      const sg = document.getElementById('searchGroup');
      const sel = document.getElementById('modelSelect');
      return JSON.stringify({
        models: (db.models || []).length,
        has_search: !!db.has_search,
        modelGroupDisplay: mg ? (mg.style.display || 'visible') : 'missing',
        searchGroupDisplay: sg ? (sg.style.display || 'visible') : 'missing',
        options: sel ? Array.from(sel.options).map(o => o.textContent).slice(0,4) : []
      });
    })()""")
    print("   parsed:", d)
    check("豆包模型控件可见",
          (d or {}).get("modelGroupDisplay") != "none",
          (d or {}).get("modelGroupDisplay"))
    check("豆包模型选项非空", len((d or {}).get("options") or []) > 0,
          (d or {}).get("options"))
    check("豆包无搜索控件（已隐藏）",
          (d or {}).get("searchGroupDisplay") == "none",
          (d or {}).get("searchGroupDisplay"))

    print()
    print("=== C) 页面里不再有硬编码的假模式名 ===")
    d = ev("""(() => {
      const sel = document.getElementById('modelSelect');
      const txt = sel ? sel.textContent : '';
      return JSON.stringify({
        hasFake: /快速模式|专家模式|识图模式/.test(txt),
        text: txt.slice(0, 60)
      });
    })()""")
    print("   parsed:", d)
    check("已无假模式名残留", (d or {}).get("hasFake") is False, (d or {}).get("text"))

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 62)
    print("RESULT: %d/%d PASS" % (passed, len(results)))
    for n, ok, det in results:
        if not ok:
            print("  FAILED:", n, "|", det)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
