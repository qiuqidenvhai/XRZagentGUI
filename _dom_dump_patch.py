# -*- coding: utf-8 -*-
"""_dom_dump_patch.py —— 给后端加一个只读的 /dom 热补丁端点，dump 当前活动浏览器
页面（DeepSeek 等）的 HTML，用于在"网页更新"后核对真实选择器（平台 DOM 标志），
不再靠猜 platforms.json 里的旧选择器。

热补丁机制与 _files_listing_fix.py 完全一致：
  - 在 terminal.py exec 完 pyc 后，由后台线程等待 GUIHandler 出现后挂上 do_GET 增强；
  - 只新增一条 GET 路由 /dom（以及 /dom?sel=<css> 可选）；其它路由原样委托；
  - 取浏览器页面：遍历 main_module 里所有活动 BrowserManager / session，找第一个
    活着的 _page，执行 page.content() 与 page.url()；没有就 404。
  - 全程 best-effort，不影响任何现有端点。
"""

import os
import re
import threading
import time
import json

_INSTALLED = False
_LOCK = threading.Lock()


def _find_live_page(main_module):
    """从 main_module 里找出第一个活着的 Playwright page（优先当前活动平台）。

    【2026-09-21 修复】多平台浏览器共存时（例如启动时起了 deepseek，后又切到元宝），
    候选里会有多个 manager 的 page 都活着。旧逻辑只返回「dir() 里第一个」，
    往往会抓到 deepseek 的 page，导致 /new_conversation、/dom、/probe 都作用在
    错误的平台页上（在元宝界面点新建对话却给 deepseek 开了个标签页）。
    现在优先选「platform_key == 当前活动平台」的那个 manager。
    """
    seen = set()
    # 1) 显式收集模块里所有带 _browser/_page 的对象（manager 类实例）
    candidates = []
    try:
        for name in dir(main_module):
            obj = getattr(main_module, name, None)
            if obj is None:
                continue
            if hasattr(obj, "_page") and hasattr(obj, "_browser"):
                candidates.append(obj)
    except Exception:
        pass
    # 2) 常见已知挂点（session / commander / browser 全局）
    for attr in ("_browser_manager", "browser", "_main_browser", "session"):
        try:
            obj = getattr(main_module, attr, None)
            if obj is not None and hasattr(obj, "_page"):
                candidates.append(obj)
        except Exception:
            pass

    def _alive(obj):
        page = getattr(obj, "_page", None)
        if page is None or id(page) in seen:
            return None
        try:
            if page.is_closed():
                return None
        except Exception:
            return None
        try:
            browser = getattr(obj, "_browser", None)
            if browser is not None and hasattr(browser, "is_closed") and browser.is_closed():
                return None
        except Exception:
            pass
        return page

    # 优先 0：commander 当前真正在用的浏览器管理器（最权威，发送/接收走的就是它）。
    #   注意：commander._bm 在任意平台都指向「默认 deepseek 管理器」，并不随平台切换变化；
    #   真正随平台切换的是 commander._session._bm（PlatformSession/DeepSeekSession 的 _bm）。
    #   之前只取 commander._bm，导致在元宝/通义等平台永远拿到 deepseek 的 page。
    pref = None
    try:
        _comm = getattr(main_module, "commander", None) or getattr(main_module, "COMMANDER", None)
        if _comm is not None:
            _sess = getattr(_comm, "_session", None)
            if _sess is not None:
                pref = getattr(_sess, "_bm", None)
            if pref is None:
                pref = getattr(_comm, "_bm", None)
    except Exception:
        pref = None
    if pref is not None:
        page = _alive(pref)
        if page is not None:
            seen.add(id(page))
            return pref, page

    # 优先 1：与「当前活动平台」匹配的 manager
    active = None
    try:
        import agent_core.platform_browser as _pb
        active = _pb._ACTIVE_PLATFORM_KEY
    except Exception:
        active = None

    if active:
        for obj in candidates:
            pk = getattr(obj, "platform_key", None)
            if pk is None:
                _prof = getattr(obj, "profile", None)
                pk = getattr(_prof, "platform", None)
            if pk == active:
                page = _alive(obj)
                if page is not None:
                    seen.add(id(page))
                    return obj, page

    # 兜底：第一个活着的 page
    for obj in candidates:
        page = _alive(obj)
        if page is not None:
            seen.add(id(page))
            return obj, page
    return None, None


def _find_running_loops():
    """找出所有正在运行的 asyncio 事件循环（跨线程）。

    后端在专门线程里 run_forever，Playwright 的 page 绑定在那个 loop 上。
    优先用 asyncio.all_active_loops()（进程级，能跨线程拿到 running loop）；
    再兜底扫 sys.modules 里挂着的 loop（is_running() 为真）。
    """
    import asyncio
    import sys
    found = []
    seen = set()
    try:
        for loop in asyncio.all_active_loops():
            if id(loop) not in seen:
                seen.add(id(loop))
                found.append(loop)
    except Exception:
        pass
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            for v in vars(mod).values():
                if isinstance(v, asyncio.AbstractEventLoop) and v.is_running() and id(v) not in seen:
                    seen.add(id(v))
                    found.append(v)
        except Exception:
            pass
    return found


def _run_async(coro, timeout=30):
    """把 Playwright 协程调度到「正在运行的」事件循环并同步取结果。"""
    import asyncio
    loops = _find_running_loops()
    if not loops:
        raise RuntimeError("未找到正在运行的 asyncio 事件循环（/dom 需在浏览器就绪后调用）")
    last_err = None
    for loop in loops:
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=timeout)
        except RuntimeError as e:
            # 该 loop 不是 page 绑定的那个（或已关），换下一个
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError("在所有运行中的事件循环上执行 /dom 均失败: %r" % last_err)


def _dom_response(self):
    """返回当前活动浏览器页面的 url + 全量 HTML（截断保护）。"""
    main_module = self._xrz_main_module
    obj, page = _find_live_page(main_module)
    if page is None:
        self._send_json(200, {"type": "ok", "url": None, "html": None,
                               "note": "当前没有活着的浏览器页面（可能尚未启动或已关闭）"})
        return
    url = None
    try:
        url = page.url
    except Exception:
        pass
    try:
        html = _run_async(page.content(), timeout=30)
    except Exception as e:
        self._send_json(200, {"type": "ok", "url": url, "html": None,
                               "note": "取 HTML 失败: %r" % e})
        return
    # 截断保护（单页面偶尔 1~3MB，回 400KB 足够核对选择器）
    if len(html) > 400000:
        html = html[:400000] + "\n<!-- ...truncated... -->"
    self._send_json(200, {"type": "ok", "url": url, "html": html})


def _probe_response(self, body: str):
    """POST /probe  {"js": "..."} —— 在【真实 agent 页面】上求值一段 JS。

    为什么需要它：/dom 只能拿静态 HTML，判断不了「选择器到底匹配到几个节点、
    innerText 到底是什么」。排查平台改版 / 思考面板抽取时，必须能在活页面上直接
    跑 JS 看返回值。这里把 JS 调度到 page 所属的那个事件循环（_run_async），
    绝不用 asyncio.run（会新建第二循环 → 跨循环调用永久挂起）。
    """
    main_module = self._xrz_main_module
    obj, page = _find_live_page(main_module)
    if page is None:
        self._send_json(200, {"type": "ok", "value": None,
                               "note": "当前没有活着的浏览器页面"})
        return
    try:
        payload = json.loads(body or "{}")
    except Exception as e:
        self._send_json(200, {"type": "error", "note": "请求体不是合法 JSON: %r" % e})
        return
    js = payload.get("js") or ""
    if not js:
        self._send_json(200, {"type": "error", "note": "缺少 js 字段"})
        return
    try:
        val = _run_async(page.evaluate(js), timeout=60)
    except Exception as e:
        self._send_json(200, {"type": "error", "note": "evaluate 失败: %r" % e})
        return
    # 统一 JSON 化：非字符串结果包一层，避免前端拿到 undefined
    if not isinstance(val, (str, int, float, bool, type(None))):
        try:
            val = json.dumps(val, ensure_ascii=False)
        except Exception:
            val = repr(val)
    self._send_json(200, {"type": "ok", "value": val,
                           "url": getattr(page, "url", None)})


def _patched_do_GET(self, main_module, orig_do_get, app_dir):
    path = getattr(self, "path", "") or ""
    clean = path.split("?", 1)[0]
    if clean == "/dom":
        try:
            self._xrz_main_module = main_module
            _dom_response(self)
            return
        except Exception as e:
            try:
                self._send_json(200, {"type": "ok", "url": None, "html": None,
                                       "note": "dom dump 异常: %r" % e})
            except Exception:
                pass
            return
    # 其它路由：原样委托
    orig_do_get(self)


def _patched_do_POST(self, main_module, orig_do_post, app_dir):
    path = getattr(self, "path", "") or ""
    clean = path.split("?", 1)[0]
    if clean == "/probe":
        try:
            self._xrz_main_module = main_module
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except Exception:
                length = 0
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            _probe_response(self, body)
            return
        except Exception as e:
            try:
                self._send_json(200, {"type": "error", "note": "probe 异常: %r" % e})
            except Exception:
                pass
            return
    orig_do_post(self)


def _do_patch_once(main_module, app_dir):
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        handler = main_module.__dict__.get("GUIHandler")
        if handler is None or not hasattr(handler, "do_GET"):
            return False
        if getattr(handler.do_GET, "_xrz_dom_patched", False):
            _INSTALLED = True
            return True
        try:
            orig = handler.do_GET
            orig_post = getattr(handler, "do_POST", None)

            def _new_do_get(self):
                _patched_do_GET(self, main_module, orig, app_dir)

            _new_do_get._xrz_dom_patched = True
            _new_do_get.__name__ = "do_GET"
            handler.do_GET = _new_do_get

            # POST /probe（活页面上跑 JS）。没有 do_POST 就跳过，不影响 /dom。
            if orig_post is not None and not getattr(orig_post, "_xrz_probe_patched", False):
                def _new_do_post(self):
                    _patched_do_POST(self, main_module, orig_post, app_dir)

                _new_do_post._xrz_probe_patched = True
                _new_do_post.__name__ = "do_POST"
                handler.do_POST = _new_do_post

            _INSTALLED = True
            marker = os.path.join(app_dir, "_dom_dump_patch_installed.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("installed_at=%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            print("[XRZ-DOM] 已安装 /dom 只读端点", flush=True)
            return True
        except Exception as e:
            print("[XRZ-DOM] 补丁安装异常（将重试）:", e, flush=True)
            return False


def install(main_module, app_dir, timeout_s=180):
    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if _do_patch_once(main_module, app_dir):
                    return
            except Exception as e:
                print("[XRZ-DOM] 安装线程异常:", e, flush=True)
            time.sleep(0.2)
        print("[XRZ-DOM] 安装超时，补丁未生效", flush=True)

    t = threading.Thread(target=_worker, daemon=True, name="xrz-dom-dump-patch")
    t.start()
    return t
