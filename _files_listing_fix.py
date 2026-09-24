"""
_files_listing_fix.py —— 仙人掌 Agent 后端「文件列表只扫上传目录」热补丁。

根因（反编译 terminal.pyc 确认）：
  GUIHandler.do_GET 的 /files 与 /attachments 两个端点只递归
  DATA_ROOT/gui_attachments（用户上传目录）。Agent 生成的任务产物
  （docx/pptx/xlsx/txt/md 等）落在 DATA_ROOT/XianRenZhang_tasks/...，
  永远不进这两个端点 → GUI 侧边栏 #fileList 永远看不到产物 →
  用户无法点击 → 产物预览功能对"自己生成的文件"实际不可达。

热补丁（在 terminal.py exec pyc 之后，由后台线程等待 GUIHandler 出现后替换）：
  1) GUIHandler.do_GET → _xrz_patched_do_GET：
       - /files、/attachments 路由命中时：合并 上传目录 + 任务产物目录（去重）
       - 其它路由：原样委托给原来的 do_GET（零侵入）
  2) GUIHandler._xrz_build_file_list 挂到类上（供 patch 复用，也可被未来代码引用）。

安全性：补丁只做 GET 路由增强；失败时回退原始 do_GET，不影响任何现有端点。
"""

import os
import threading
import time
from pathlib import Path

_INSTALLED = False
_LOCK = threading.Lock()

# 产物白名单（避免把浏览器 profile、会话 json 等大目录混进侧边栏）
_ART_EXT = {
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".csv",
    ".pdf", ".txt", ".md", ".html", ".htm", ".json", ".js", ".py",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
}
# 递归时跳过的大目录
_SKIP_DIRS = {"conversations", "buffers", ".xianrenzhang_agent"}


def _scan_roots(xrz_paths, attach_dir, max_files=500):
    """递归列出 上传目录 + 任务目录 里可预览/可下载的产物文件。"""
    results = []
    seen = set()

    def _walk(root):
        if not root or not Path(root).exists():
            return
        for f in Path(root).rglob("*"):
            if len(results) >= max_files:
                return
            try:
                if not f.is_file():
                    continue
                rel = str(f.relative_to(Path(root)))
                parts = Path(rel).parts
                if parts and parts[0] in _SKIP_DIRS:
                    continue
                if f.suffix.lower() not in _ART_EXT:
                    continue
                if f.name.startswith("."):
                    continue
                p = str(f).replace("\\", "/")
                if p in seen:
                    continue
                seen.add(p)
                results.append({"path": p, "name": f.name, "size": f.stat().st_size})
            except Exception:
                continue

    _walk(attach_dir)
    # 任务产物目录（docx_create / pptx_create / file_write 等的默认落地处）
    try:
        _walk(getattr(xrz_paths, "TASKS_DIR", None))
    except Exception:
        pass
    return results


def _conversation_artifacts_path():
    """每个会话记录自己产物路径的索引文件。"""
    try:
        from agent_core import xrz_paths
        return xrz_paths.XRZ_AGENT_DIR / "conversation_artifacts.json"
    except Exception:
        return None


def _load_artifact_index():
    import json
    p = _conversation_artifacts_path()
    if not p or not Path(p).exists():
        return {}
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def record_artifacts(conversation_id, paths):
    """把一批产物路径登记到某个会话名下（供 backend 在任务完成时调用）。

    幂等：重复登记同一路径不产生重复项。空 conversation_id 直接忽略，
    避免把产物记到「没有任务」的全局桶里。
    """
    import json
    if not conversation_id or not paths:
        return
    p = _conversation_artifacts_path()
    if not p:
        return
    try:
        with _LOCK:
            idx = _load_artifact_index()
            bucket = idx.setdefault(str(conversation_id), [])
            for raw in paths:
                if not raw:
                    continue
                norm = str(raw).replace("\\", "/")
                if norm not in bucket:
                    bucket.append(norm)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            Path(tmp).write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            os.replace(tmp, p)
    except Exception:
        pass


def rebuild_registry_from_disk(force=False):
    """把「磁盘上已有产物」回填进 registry（数据修复，只在 registry 空时自动跑）。

    背景：registry（conversation_artifacts.json）是产物归属的唯一索引，但它曾经
    被测试夹具/清理脚本写成 {}，于是 500+ 个历史任务在 GUI 右侧「文件产物」里
    全部看不到东西（磁盘上其实有 600+ 个产物文件）。
    这里反向扫描 conversations/*.json，把每个会话引用到的、且真实存在的产物
    重新登记回去。原文件先备份成 .bak。
    """
    import json
    p = _conversation_artifacts_path()
    if not p:
        return 0, "no registry path"
    try:
        cur = _load_artifact_index()
    except Exception:
        cur = {}
    if cur and not force:
        return 0, "registry 非空，跳过"

    try:
        from agent_core import xrz_paths
    except Exception as e:
        return 0, "xrz_paths 不可用: %s" % e

    conv_dir = Path(xrz_paths.CONVERSATIONS_DIR)
    if not conv_dir.exists():
        return 0, "会话目录不存在"

    idx = dict(cur)
    total = 0
    convs = 0
    for f in sorted(conv_dir.glob("conv_*.json")):
        cid = f.stem[5:]
        try:
            conv = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        try:
            items = _paths_from_conversation(conv, xrz_paths)
        except Exception:
            continue
        if not items:
            continue
        bucket = idx.setdefault(cid, [])
        for it in items:
            if it["path"] not in bucket:
                bucket.append(it["path"])
                total += 1
        convs += 1

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if Path(p).exists():
            try:
                bak = Path(str(p) + ".bak")
                bak.write_text(Path(p).read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        tmp = str(p) + ".tmp"
        Path(tmp).write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        return 0, "写入失败: %s" % e
    return total, "已回填 %d 个产物，覆盖 %d 个会话" % (total, convs)


def _current_conversation_id():
    """从 pyc 后端的全局状态里取「当前会话 id」。

    terminal.pyc 把 session 对象挂在模块/全局上，命名不固定，
    所以这里按多个候选名逐个探测，取到第一个非空字符串。
    """
    try:
        import sys as _sys
        cands = []
        main = _sys.modules.get("__main__")
        if main is not None:
            for name in ("SESSION_ID", "CURRENT_SESSION_ID", "current_session_id",
                         "conversation_id", "CURRENT_CONVERSATION_ID"):
                v = getattr(main, name, None)
                if isinstance(v, str) and v:
                    cands.append(v)
            sess = getattr(main, "session", None) or getattr(main, "SESSION", None)
            if sess is not None:
                for name in ("conversation_id", "session_id", "current_conversation_id",
                             "conv_id", "task_id"):
                    v = getattr(sess, name, None)
                    if isinstance(v, str) and v:
                        cands.append(v)
        return cands[0] if cands else ""
    except Exception:
        return ""


def _newest_task_artifacts(limit=200):
    """无当前会话时（如刚启动/新对话）：列出最近一次任务的产物。

    通过 tasks.json 拿到最后一个任务的 id，再从其会话 JSON 里提取
    该任务引用的产物路径（会话 messages 里出现过的绝对/相对路径）。
    """
    import json
    try:
        from agent_core import xrz_paths
    except Exception:
        return []
    out = []
    try:
        tj = xrz_paths.TASKS_INDEX_PATH
        if not Path(tj).exists():
            return []
        data = json.loads(Path(tj).read_text(encoding="utf-8"))
        tasks = data.get("tasks") if isinstance(data, dict) else data
        if not tasks:
            return []
        last = tasks[-1]
        cf = last.get("file")
        if cf and Path(cf).exists():
            conv = json.loads(Path(cf).read_text(encoding="utf-8"))
            out = _paths_from_conversation(conv, xrz_paths)
    except Exception:
        pass
    return out[:limit]


def _paths_from_conversation(conv, xrz_paths):
    """从会话 JSON 的 messages 里抽出被创建/引用的产物文件路径。"""
    import re
    found = []
    seen = set()
    msgs = (conv or {}).get("messages") or []
    pat = re.compile(r'"(?:path|file|filename|output)"\s*:\s*"([^"]+)"')
    for m in msgs:
        content = m.get("content") or ""
        for cand in pat.findall(content):
            p = cand.replace("\\\\", "/").replace("\\", "/")
            full = Path(p)
            if not full.is_absolute():
                full = xrz_paths.TASKS_DIR / p
            try:
                if full.is_file() and full.suffix.lower() in _ART_EXT:
                    s = str(full).replace("\\", "/")
                    if s not in seen:
                        seen.add(s)
                        found.append({"path": s, "name": full.name,
                                      "size": full.stat().st_size})
            except Exception:
                continue
    return found


def _serve_docx_preview(self, path):
    """对 .docx 产物返回正文预览 html。成功返回 True（已写响应），否则 False。"""
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    raw_path = unquote((qs.get("path") or [""])[0])
    if not raw_path.lower().endswith(".docx"):
        return False
    full = raw_path
    if not os.path.exists(full):
        try:
            from agent_core import xrz_paths
            cand = str(xrz_paths.DATA_ROOT / raw_path.lstrip("/"))
            if os.path.exists(cand):
                full = cand
        except Exception:
            pass
    if not os.path.exists(full):
        return False  # 让原始 do_GET 处理「文件不存在」

    import docx as _docx
    d = _docx.Document(full)
    blocks = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style = (p.style.name or "").lower() if p.style else ""
        if "title" in style or "heading 1" in style:
            blocks.append("<h2>%s</h2>" % _html_escape(t))
        elif "heading" in style:
            blocks.append("<h3>%s</h3>" % _html_escape(t))
        else:
            blocks.append("<p>%s</p>" % _html_escape(t))
    for tbl in d.tables:
        rows_html = []
        for ri, row in enumerate(tbl.rows):
            tag = "th" if ri == 0 else "td"
            cells = "".join("<%s>%s</%s>" % (tag, _html_escape(c.text.strip()), tag) for c in row.cells)
            rows_html.append("<tr>%s</tr>" % cells)
        if rows_html:
            blocks.append(
                "<table style='border-collapse:collapse;width:100%;font-size:12px;margin:8px 0'>"
                "".join(rows_html) + "</table>")
    body = "\n".join(blocks) or "<p>（文档无正文）</p>"
    html = (
        "<div style='padding:16px;font-size:13px;line-height:1.6'>"
        "<div style='margin-bottom:10px;color:var(--text2);font-size:12px'>"
        "📘 %s</div>%s</div>" % (_html_escape(os.path.basename(full)), body))
    self._send_json(200, {"type": "ok", "html": html, "editable": False,
                           "path": full, "kind": "docx_text"})
    return True


def _html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _conv_id_variants(cid):
    """同一个任务的 id 在项目里有多种写法，这里穷举出来。

    【实测】tasks.json / 前端 TASKS 里的 id 形如
        deepseek_20260812_113400_60879   （末尾多 5 位随机数）
    而会话文件名是
        conv_deepseek_20260812_113400.json（没有那 5 位）
    → 前端点任务时把带尾巴的 id 传给 /attachments，后端按 conv_<id>.json
      找文件必然找不到 → 右侧「文件产物」对历史任务永久为空。
    这里同时给出 原样 / 去尾 / 去 conv_ 前缀 三类变体。
    """
    import re
    cid = str(cid or "").strip()
    if not cid:
        return []
    out = [cid]
    base = re.sub(r"_\d{5}$", "", cid)
    if base and base != cid:
        out.append(base)
    for v in list(out):
        if v.startswith("conv_"):
            out.append(v[5:])
    dedup = []
    for v in out:
        if v and v not in dedup:
            dedup.append(v)
    return dedup


def _load_conversation_json(xrz_paths, cid):
    """按多种 id 写法定位该任务的会话 JSON，返回解析后的 dict 或 None。"""
    import json
    # 1) 按文件名直接找
    for v in _conv_id_variants(cid):
        for name in ("conv_%s.json" % v, "%s.json" % v):
            try:
                p = Path(xrz_paths.CONVERSATIONS_DIR) / name
                if p.exists():
                    return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    # 2) 用 tasks.json 的 id → file 映射（最可靠，文件名规则再变也不怕）
    try:
        tj = Path(xrz_paths.TASKS_INDEX_PATH)
        if tj.exists():
            data = json.loads(tj.read_text(encoding="utf-8"))
            tasks = data.get("tasks") if isinstance(data, dict) else data
            wanted = set(_conv_id_variants(cid))
            for t in (tasks or []):
                if str(t.get("id")) in wanted:
                    f = t.get("file")
                    if f and Path(f).exists():
                        return json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _registry_buckets(idx, cid):
    """从 registry 里取出属于该任务的所有 bucket（容忍 id 写法不一致）。"""
    out = []
    if not isinstance(idx, dict):
        return out
    variants = _conv_id_variants(cid)
    keys = []
    for v in variants:
        if v in idx:
            keys.append(v)
    # 前缀匹配：registry 里存的是带尾巴的 id，调用方给的是短的（或反之）
    if not keys:
        for k in idx.keys():
            for v in variants:
                if k.startswith(v + "_") or v.startswith(str(k) + "_"):
                    keys.append(k)
                    break
    for k in keys:
        bucket = idx.get(k)
        if isinstance(bucket, dict):
            bucket = bucket.get("paths") or bucket.get("files") or []
        if isinstance(bucket, (str, bytes)):
            bucket = [bucket]
        out.extend(bucket or [])
    return out


def _conversation_scoped_files(conversation_id):
    """按会话范围产出文件列表。

    规则（对齐「任务产物 = 任务私有」的产品语义）：
      - 有 conversation_id：只返回该会话名下的产物（registry 命中）+ 该会话
        记录里引用过的产物（历史任务兜底）。不返回其它任务的产物，也不返回
        任务目录里的散落文件。
      - 没有 conversation_id（新对话 / 未选任务）：返回空列表 ——
        「没有任务的时候就不能有任务产物」。
    """
    try:
        from agent_core import xrz_paths
    except Exception:
        return []

    if not conversation_id:
        return []

    out = []
    seen = set()

    def _push(raw):
        try:
            f = Path(raw)
            if f.is_file():
                s = str(f).replace("\\", "/")
                if s not in seen:
                    seen.add(s)
                    out.append({"path": s, "name": f.name,
                                "size": f.stat().st_size})
        except Exception:
            pass

    # 1) 该会话登记的产物（权威来源）
    # registry 的每个 bucket 既可能是裸路径列表（record_artifacts 写入），
    # 也可能是 {"paths": [...], "updated_at": ...} 包装（早期版本 / 手工写入）。
    # 两种都要吃得下，否则换一种写法就静默丢产物。
    for raw in _registry_buckets(_load_artifact_index(), conversation_id):
        _push(raw)

    # 2) 该会话的会话 JSON 里引用到的产物（兜底：registry 尚未覆盖的历史任务）
    try:
        conv = _load_conversation_json(xrz_paths, conversation_id)
        if conv:
            for item in _paths_from_conversation(conv, xrz_paths):
                if item["path"] not in seen:
                    seen.add(item["path"])
                    out.append(item)
    except Exception:
        pass

    return out


def _sync_session_cmd(main_module, payload):
    """本地执行会话管理（新建 / 切换 / 删除会话）。

    旧实现是前端把 '@@@@{"tool":"session_manager",...}@@@@' 当 command 发给
    /command，于是这串内部协议会被当成一条消息投给模型并显示出来。
    这里改成直接调用后端的会话管理逻辑，纯本地、即时、不进对话。

    返回 (ok, message)。
    """
    action = (payload or {}).get("action")
    sid = (payload or {}).get("id")

    # 1) 优先找 pyc 后端里现成的会话管理器 / 工具入口
    mgr = None
    for name in ("session_manager", "SESSION_MANAGER", "session", "sessions"):
        obj = getattr(main_module, name, None)
        if obj is not None and (hasattr(obj, "create") or hasattr(obj, "switch")
                                or hasattr(obj, "delete")):
            mgr = obj
            break
    if mgr is None:
        for k, v in list(getattr(main_module, "__dict__", {}).items()):
            if hasattr(v, "create") and hasattr(v, "switch") and hasattr(v, "delete"):
                mgr = v
                break

    if mgr is not None:
        try:
            if action == "create":
                r = mgr.create()
                return True, "已新建会话%s" % (("：%s" % r) if r else "")
            if action == "switch" and sid is not None:
                mgr.switch(sid)
                return True, "已切换到会话 %s" % sid
            if action == "delete" and sid is not None:
                mgr.delete(sid)
                return True, "已删除会话 %s" % sid
        except Exception as e:
            return False, "会话操作失败：%s" % e

    # 2) 退而求其次：会话历史是文件制的，直接操作记录
    try:
        from agent_core.session import get_conversation_history as _gch
        hist = _gch()
        if action == "delete" and sid is not None:
            if hasattr(hist, "delete"):
                hist.delete(sid)
                return True, "已删除会话 %s" % sid
            return False, "会话历史不支持删除"
        if action in ("create", "switch"):
            return True, "已切换会话上下文（本地）"
    except Exception as e:
        return False, "会话模块不可用：%s" % e

    return False, "未知的会话操作：%s" % action


def _find_live_agent(main_module):
    """在 pyc 后端的全局里找出当前活动的 Agent / 平台浏览器对象。

    命名不固定，所以按候选名 + 结构特征逐个探测（有 start_new_conversation 的
    就是我们要的）。找不到返回 None。
    """
    cands = []

    def _scan(obj, depth=0):
        if obj is None or depth > 3:
            return
        if hasattr(obj, "start_new_conversation"):
            cands.append(obj)
        return

    for name in ("agent", "AGENT", "current_agent", "session", "SESSION",
                 "commander", "COMMANDER", "bot", "BOT"):
        obj = getattr(main_module, name, None)
        _scan(obj)

    # 也扫一遍模块 __dict__ 里带 start_new_conversation 的对象
    for k, v in list(getattr(main_module, "__dict__", {}).items()):
        try:
            if hasattr(v, "start_new_conversation"):
                cands.append(v)
            # 容器里的元素
            if isinstance(v, (list, tuple, set)):
                for it in v:
                    if hasattr(it, "start_new_conversation"):
                        cands.append(it)
            elif isinstance(v, dict):
                for it in v.values():
                    if hasattr(it, "start_new_conversation"):
                        cands.append(it)
        except Exception:
            continue

    return cands[0] if cands else None


def _run_async_on_backend_loop(coro, timeout=180):
    """把协程调度到 **后端自己的** 那个正在运行的 asyncio 循环上并等结果。

    【关键】绝不能自己 asyncio.run() —— 那会新建第二个事件循环，而 Playwright
    的 page/context 绑定在后端原有的那个循环上，跨循环操作会永久阻塞（实测：
    /new_conversation 请求挂死、后端连日志都不打）。
    复用 _dom_dump_patch 已验证的 _run_async（它会遍历所有 running loop，
    挑出真正绑定 page 的那个），失败再退回本地实现。
    """
    # 1) 优先复用已验证的实现
    try:
        import _dom_dump_patch as _dom
        return _dom._run_async(coro, timeout=timeout)
    except Exception:
        pass

    # 2) 本地兜底：自己找 running loop
    import asyncio
    import sys as _sys

    loops = []
    seen = set()
    try:
        for lp in asyncio.all_active_loops():
            if id(lp) not in seen:
                seen.add(id(lp))
                loops.append(lp)
    except Exception:
        pass
    for mod in list(_sys.modules.values()):
        if mod is None:
            continue
        try:
            for v in vars(mod).values():
                if (isinstance(v, asyncio.AbstractEventLoop) and v.is_running()
                        and id(v) not in seen):
                    seen.add(id(v))
                    loops.append(v)
        except Exception:
            pass

    if not loops:
        raise RuntimeError("未找到正在运行的 asyncio 事件循环（后端可能仍在启动）")

    last_err = None
    for lp in loops:
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, lp)
            return fut.result(timeout=timeout)
        except RuntimeError as e:
            last_err = e
            continue
    raise RuntimeError("所有事件循环都无法执行该操作：%s" % last_err)


def _live_session_objects(main_module):
    """找出后端里所有「持有对话上下文」的活会话对象。

    【为什么需要这个 —— 2026-09-21 实测到的严重 BUG】
      原来的 /new_conversation 只做了「让浏览器页面跳到平台的新对话页」，
      **完全没有清后端自己的 self._messages**。后果很隐蔽但极严重：
        - 页面看着是新的、界面也清空了（用户以为成功了）；
        - 但后端下一轮 _build_context_for_send() 仍把【整个旧对话】拼成
          prompt 发给模型 → 模型在一堆历史"只回复X"的指令里挑一条旧的回，
          于是出现「用户在 GUI 里发 A，AI 回的是上一轮的 B」这种鬼打墙；
        - 而且所有轮次都被写进【同一个】会话 JSON 文件，任务归属全乱。

      判据：既是 session 语义（有 _messages / clear_history），又不是浏览器管理器。
    """
    found = []
    seen = set()

    def _consider(obj):
        if obj is None or id(obj) in seen:
            return
        try:
            has_msgs = hasattr(obj, "_messages")
            has_clear = hasattr(obj, "clear_history")
            is_bm = hasattr(obj, "_browser") and hasattr(obj, "_page")
        except Exception:
            return
        # 会话对象：有消息列表/清空能力，且不是纯浏览器管理器
        if (has_msgs or has_clear) and not is_bm:
            seen.add(id(obj))
            found.append(obj)

    # 1) 模块全局直接挂着（最常见）
    try:
        for name in dir(main_module):
            try:
                _consider(getattr(main_module, name, None))
            except Exception:
                continue
    except Exception:
        pass

    # 2) 已知挂点 + 容器内元素
    for attr in ("session", "SESSION", "agent", "AGENT", "commander", "COMMANDER"):
        try:
            v = getattr(main_module, attr, None)
        except Exception:
            continue
        _consider(v)
        try:
            if isinstance(v, (list, tuple, set)):
                for it in v:
                    _consider(it)
            elif isinstance(v, dict):
                for it in v.values():
                    _consider(it)
            # 对象的常见内部挂点（agent._session 等）
            elif v is not None:
                for sub in ("session", "_session", "agent", "_agent"):
                    try:
                        _consider(getattr(v, sub, None))
                    except Exception:
                        continue
        except Exception:
            continue

    # 3) 顺着「活页面」反查：持 page 的对象的 _session 属性
    try:
        import _dom_dump_patch as _dom
        obj, _page = _dom._find_live_page(main_module)
        if obj is not None:
            for sub in ("_session", "session", "_agent", "agent"):
                try:
                    _consider(getattr(obj, sub, None))
                except Exception:
                    continue
    except Exception:
        pass

    return found


def _reset_backend_context(main_module):
    """把后端所有活会话的上下文清空（新对话 = 新上下文，真正的独立窗口）。

    返回 (清空的对象数, 描述)。同时也清会话 id / 动作日志 / 已保存路径，
    否则「新对话」的第一轮仍会覆盖写回上一个任务的 json 文件。
    """
    objs = _live_session_objects(main_module)
    n = 0
    detail = []
    for o in objs:
        try:
            # 优先用官方方法（会把 _session_id 一起清掉）
            if hasattr(o, "clear_history"):
                o.clear_history()
            else:
                try:
                    o._messages.clear()
                except Exception:
                    pass
            # 这些字段若残留，「新对话」的产物/记录仍会挂到旧任务上。
            # 【关键】_conv_file_path 是「本次会话写回哪个 json」的缓存，
            # 不清的话新对话的内容会继续写进上一个任务的文件里
            # （实测：新建对话后仍写回 conv_deepseek_<旧时间戳>.json，
            #   导致历史记录里新旧两个任务混成一条）。
            for fld, val in (("_session_id", ""), ("_current_file", None),
                             ("_conversation_file", None), ("_saved_path", None),
                             ("_conv_file_path", ""), ("_restored_from", None)):
                try:
                    if hasattr(o, fld):
                        setattr(o, fld, val)
                except Exception:
                    pass
            # 兜底：任何名字里带 conv_file / conversation_path 的字符串缓存也清掉
            # （字段名可能随版本变化，硬编码列表会漏）
            try:
                for attr in list(vars(o).keys()):
                    low = attr.lower()
                    if ("conv_file" in low or "conversation_path" in low
                            or low.endswith("_saved_file")):
                        try:
                            setattr(o, attr, "")
                        except Exception:
                            continue
            except Exception:
                pass
            # 动作日志（[执行进度]）也必须清，否则模型看到上一轮的动作摘要
            try:
                if hasattr(o, "_action_log"):
                    o._action_log = []
            except Exception:
                pass
            n += 1
            detail.append(type(o).__name__)
        except Exception as e:
            detail.append("ERR:%s" % e)
    return n, ",".join(detail) or "无会话对象"


def _prune_orphan_browser_session(main_module):
    """浏览器侧也补一刀：清掉浏览器管理器里缓存的「上次回复/消息」状态。

    有些平台实现会把上一次的 reply 文本存在管理器上，新对话后如果不清，
    wait_response() 可能立刻把旧文本当成新回复返回（表现为「秒回旧答案」）。
    """
    hits = []
    try:
        import _dom_dump_patch as _dom
        obj, _page = _dom._find_live_page(main_module)
        if obj is not None:
            for fld in ("_last_response", "last_response", "_cached_reply",
                        "_last_reply", "_response_cache"):
                try:
                    if hasattr(obj, fld):
                        setattr(obj, fld, None)
                        hits.append(fld)
                except Exception:
                    continue
    except Exception:
        pass
    return hits


def _sync_new_conversation(main_module):
    """同步开新对话 = 换页面 **且** 清空后端上下文，做完才返回。

    【2026-09-21 重要修复】旧实现只做「页面跳到新对话页」，后端 self._messages
    一条没清 → 下一轮仍把整个旧对话拼进 prompt，模型读到一堆历史指令后
    随机挑一条旧的回（实测：GUI 里发「产物归属测试」，AI 回「上下文测试C」）。
    现在必须双管齐下：清上下文 + 换页面，缺一不可。

    【为什么不调 agent.start_new_conversation()】实测踩坑：
      1) 按名字在 main_module 全局里找带 start_new_conversation 的对象，找到的
         往往不是绑定页面的那个（agent 与 browser manager 是两个对象），调用它
         毫无效果 —— 接口 0.1s 回「成功」，但页面 URL 和消息一条没变。
      2) pyc 里 DeepSeekSession 的签名与源码里 PlatformSession 不一致（没有
         keep_user_task 参数），硬传会 TypeError。
    所以改为**直接操作真正的活页面** + **直接清活会话对象**，并且逐项验证。
    """
    import re as _re
    import time as _time

    # ── 第一步：清空后端上下文（这才是「独立上下文窗口」的关键）──
    reset_n, reset_detail = 0, "无会话对象"
    try:
        reset_n, reset_detail = _reset_backend_context(main_module)
    except Exception as e:
        reset_detail = "清上下文异常: %s" % e
    # 【两重方案 2026-09-24】新建对话除了换浏览器窗口，GUI/后端的「当前任务」
    # 也必须换成全新的：不清的话下一条消息的产物/记忆还挂在上一个任务名下，
    # 用户感知就是「新建对话还是没有、还是老任务」。
    try:
        from agent_core.session import set_current_task_id as _set_tid
        _set_tid(None)
    except Exception:
        pass
    pruned = []
    try:
        pruned = _prune_orphan_browser_session(main_module)
    except Exception:
        pruned = []

    # 1) 找到真正活着的页面对象
    page = None
    profile = None
    try:
        import _dom_dump_patch as _dom
        obj, page = _dom._find_live_page(main_module)
        if obj is not None:
            profile = getattr(obj, "profile", None)
    except Exception:
        page = None

    if page is None:
        # 页面没找到也算部分成功：上下文已清（模型不会再读到旧对话）
        if reset_n:
            return True, ("已清空对话上下文（%d 个会话对象），但未找到活动页面，"
                          "请在平台窗口手动点「新对话」。" % reset_n)
        return False, "找不到活动的浏览器页面（后端可能仍在启动，请稍后再试）"

    # 2) 目标 = 该平台的新对话地址
    target = None
    try:
        target = getattr(profile, "chat_url", None) or getattr(profile, "url", None)
    except Exception:
        target = None
    if not target:
        target = "https://chat.deepseek.com"

    # 3) 记录旧会话 id（用于事后验证真的换了会话）
    try:
        old_url = page.url or ""
    except Exception:
        old_url = ""
    m = _re.search(r"/a/chat/s/([0-9a-fA-F\-]{8,})", old_url)
    old_id = m.group(1) if m else None

    # 4) 开一个【真正的新标签页】= 字面意义上的「新的上下文窗口」
    #    —— 关键修复：旧实现只在「同一个」页面上 page.goto 跳走，用户感知就是
    #       「没有新开窗口、上下文没换」。现在改成在【同一浏览器上下文里新开一个
    #       标签页】(page.context.new_page())：旧对话页原样新建一轮，新对话在新标签页
    #       里真正新建，后续所有发送/看界面都走新页，旧页随后收起（已存进历史记录）。
    _ctx = None
    try:
        _ctx = getattr(page, "context", None)
    except Exception:
        _ctx = None
    old_pages = 1
    if _ctx is not None:
        try:
            old_pages = len(_ctx.pages)
        except Exception:
            old_pages = 1

    async def _open_new_tab():
        if _ctx is None:
            return None
        return await _ctx.new_page()

    new_page = None
    try:
        new_page = _run_async_on_backend_loop(_open_new_tab(), timeout=60)
    except Exception as _e:
        print("[new_conv] 开新标签页失败，回退同页跳转: %s" % _e, flush=True)
        new_page = None

    if new_page is None:
        # 回退：同页 goto（保持旧行为兜底）
        async def _nav():
            await page.goto(target, wait_until="commit", timeout=60000)
        try:
            _run_async_on_backend_loop(_nav(), timeout=120)
        except Exception as _e:
            if reset_n:
                return True, ("已清空对话上下文（%d 个会话对象），但页面跳转失败：%s" % (reset_n, _e))
            return False, "新建对话失败（导航）：%s" % _e
        active_page = page
    else:
        active_page = new_page

    # 5) 在新标签页里导航到「新对话」并点开平台自带的新建对话按钮，确保是真正全新的一轮
    async def _init_new_tab():
        await active_page.goto(target, wait_until="domcontentloaded", timeout=60000)
        selectors = [
            "a:has-text('新对话')", "button:has-text('新对话')",
            "a:has-text('新建对话')", "button:has-text('新建对话')",
            "button:has-text('New Chat')", "a:has-text('New Chat')",
            "[data-testid='new-chat']", "[aria-label*='新对话']",
            "a[href='/']",
        ]
        for sel in selectors:
            try:
                btn = active_page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    await active_page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        # 清空输入框，避免旧草稿残留
        for sel in ("textarea", "div[contenteditable='true']"):
            try:
                el = await active_page.query_selector(sel)
                if el:
                    await el.fill("")
            except Exception:
                continue

    try:
        _run_async_on_backend_loop(_init_new_tab(), timeout=120)
    except Exception as _e:
        print("[new_conv] 初始化新标签页部分步骤失败: %s" % _e, flush=True)

    # 6) 把活动页面切到新标签页（后续 /dom、发送、截图都走新页）
    if obj is not None and new_page is not None:
        try:
            setattr(obj, "_page", new_page)
        except Exception:
            pass

    # 7) 收尾校验：确认确实多了一个标签页、且已离开旧会话
    new_url = ""
    try:
        new_url = (new_page or page).url or ""
    except Exception:
        new_url = ""
    new_pages = old_pages
    try:
        _ctx1 = getattr(new_page or page, "context", None)
        if _ctx1 is not None:
            new_pages = len(_ctx1.pages)
    except Exception:
        new_pages = old_pages

    if old_id and old_id in new_url and new_page is None:
        return False, ("新建对话未生效：页面仍停留在旧会话（%s…）。"
                       "请确认该平台已登录后重试。" % old_id[:8])

    # 8) 收起旧对话页（之前的对话已存进「历史记录」，避免标签页越开越多）
    if new_page is not None:
        try:
            async def _close_old():
                await page.close()
            _run_async_on_backend_loop(_close_old(), timeout=15)
        except Exception:
            pass

    tail = "并已清空后端对话上下文（%d 个：%s）" % (reset_n, reset_detail)
    if pruned:
        tail += "；同时清理了浏览器缓存回复字段 %s" % ",".join(pruned)
    if new_page is not None:
        note = "，并在浏览器里新开了一个标签页作为独立上下文窗口（标签页 %d→%d，原对话页已收起）" % (old_pages, new_pages)
    else:
        note = ""
    return True, ("已开启一个全新的独立对话（新标签页" + note + "，" + tail + "）")



def _local_gui_answer(path):
    """GUI 本地按钮（状态 / 帮助）的回答。

    这些是**软件自身的功能**，必须在点击瞬间由本地代码给出，绝不能走
    /command 变成一条消息发给模型 —— 用户明确要求「GUI 点击马上反应，
    不要显示指令，更不要发给 AI」。
    """
    import time as _time

    if path == "/status":
        lines = ["📊 仙人掌 Agent 运行状态", ""]

        # 后端 / Agent
        try:
            import urllib.request as _u
            with _u.urlopen("http://127.0.0.1:8888/health", timeout=2) as r:
                import json as _j
                h = _j.loads(r.read().decode("utf-8") or "{}")
            lines.append("· 后端：运行中（端口 %s）" % h.get("port", "8888"))
            lines.append("· Agent：%s" % ("就绪" if h.get("agent_ready") else "启动中"))
            lines.append("· 浏览器：%s" % ("已连接" if h.get("browser") == "connected" else h.get("browser", "未知")))
            lines.append("· 当前平台：%s" % h.get("platform", "未知"))
        except Exception:
            lines.append("· 后端：不可达（请检查 terminal.py 是否在运行）")

        # 当前会话 / 任务产物
        try:
            cid = _current_conversation_id()
            lines.append("· 当前会话：%s" % (cid or "无（新对话）"))
            files = _conversation_scoped_files(cid) if cid else []
            lines.append("· 本次任务产物：%d 个" % len(files))
            for f in files[:8]:
                lines.append("    - %s" % f.get("name"))
        except Exception:
            pass

        lines.append("")
        lines.append("· 时间：%s" % _time.strftime("%Y-%m-%d %H:%M:%S"))
        return {"type": "system", "text": "\n".join(lines)}

    # /help
    text = "\n".join([
        "❓ 仙人掌 Agent 使用帮助",
        "",
        "【发消息】",
        "  在底部输入框打字，按 Enter 发送，Shift+Enter 换行。",
        "  消息会发给你当前选中的平台网页（DeepSeek / 通义 / 豆包 / 元宝）。",
        "",
        "【新建对话】",
        "  点「➕ 新建对话」= 开一个全新的独立对话（独立上下文 + 独立任务产物）。",
        "  旧对话会自动保留在「📚 历史记录」里，随时可以点回去继续。",
        "",
        "【任务产物】",
        "  产物按任务隔离：点进哪个任务就只显示那个任务的产物。",
        "  没有任务时，产物栏是空的。",
        "",
        "【平台切换】",
        "  左上角平台的按钮直接切；切平台会各用各的登录态。",
        "",
        "【常用按钮】",
        "  ▶ 发送 · ⏹ 中断任务 · 💬 插话（不等任务完成）",
        "  📎 上传文件 · 👁 文件预览 · 📊 状态 · ❓ 帮助",
        "",
        "提示：本帮助由软件本地即时显示，不会发给 AI。",
    ])
    return {"type": "system", "text": text}


def _reveal_in_explorer(self, path):
    """GET /reveal?path=... → 在 Windows 资源管理器中定位（选中）该文件。

    路径不存在 / 目录 → 打开所在目录；文件存在 → explorer /select。
    返回 True 表示已处理（命中本路由），False 表示让调用方继续走原 do_GET。
    """
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(getattr(self, "path", "") or "")
    if parsed.path != "/reveal":
        return False
    qs = parse_qs(parsed.query)
    target = unquote((qs.get("path") or [""])[0])
    ok, msg = False, ""
    try:
        import subprocess
        import sys
        if not target:
            ok, msg = False, "缺少 path 参数"
        elif not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent and os.path.isdir(parent):
                target = parent
                subprocess.Popen(['explorer', '/root,0', target],
                                 creationflags=0x08000000)  # CREATE_NO_WINDOW
                ok, msg = True, "文件不存在，已打开所在目录"
            else:
                ok, msg = False, "路径不存在或不可访问"
        else:
            # explorer /select 选中文件本身；后台拉进程、立即返回。
            # CREATE_NO_WINDOW 让 explorer 不在黑框里闪一下。
            subprocess.Popen(['explorer', '/select,', os.path.abspath(target)],
                             creationflags=0x08000000)  # CREATE_NO_WINDOW
            ok, msg = True, "已定位"
    except Exception as e:
        ok, msg = False, str(e)
    try:
        self._send_json(200 if ok else 400,
                        {"type": "ok" if ok else "error",
                         "ok": ok, "text": msg, "path": target})
    except Exception:
        pass
    return True


def _patched_do_GET(self, main_module, orig_do_get, app_dir):
    """增强 /files、/attachments（按会话列产物）与 /preview（docx 正文）。"""
    from urllib.parse import urlparse, parse_qs
    path = getattr(self, "path", "") or ""
    parsed = urlparse(path)

    # 0) GET /reveal → 在资源管理器中定位文件（软件本地即时行为，不走 AI）
    if _reveal_in_explorer(self, path):
        return

    # 1) 文件列表：严格按「当前会话」过滤（无会话 → 空）
    if parsed.path in ("/files", "/attachments"):
        try:
            qs = parse_qs(parsed.query)
            explicit = (qs.get("conversation_id") or qs.get("conv") or [""])[0]
            conv_id = explicit or _current_conversation_id()
            files = _conversation_scoped_files(conv_id)
            self._send_json(200, {"type": "ok", "files": files,
                                  "conversation_id": conv_id})
            return
        except Exception as e:
            try:
                self.send_error(500, "获取文件列表失败: %s" % e)
            except Exception:
                pass
            return

    # 2) 本地 /status、/help：GUI 自己的按钮必须**立刻**响应，不能当成指令发给 AI。
    #    （旧实现是 sendCmd('status')/sendCmd('help') → 走 /command → 变成一条消息
    #     发给模型，既慢又出现在对话区，用户明确禁止这种行为。）
    if parsed.path in ("/status", "/help"):
        try:
            self._send_json(200, _local_gui_answer(parsed.path))
            return
        except Exception as e:
            try:
                self.send_error(500, "本地命令失败: %s" % e)
            except Exception:
                pass
            return

    # 2b) POST-only 端点的 GET 兜底：明确告知而不是静默 404
    if parsed.path in ("/new_conversation",):
        try:
            self._send_json(405, {"type": "error",
                                  "text": "请用 POST 调用 %s" % parsed.path})
        except Exception:
            pass
        return

    # 3) docx 正文预览增强：pyc 原 /preview 对 docx 只回"文件名+下载"，
    #    这里用 python-docx 提取正文（段落+表格）返回可读 html；失败则回退原实现。
    if path.startswith("/preview"):
        try:
            if _serve_docx_preview(self, path):
                return
        except Exception:
            pass

    # 4) 其它路由：原样走原 do_GET（保留 /events /health /download 等全部行为）
    orig_do_get(self)


def _patched_do_POST(self, main_module, orig_do_post, app_dir):
    """新增 POST /register_artifacts：把产物路径登记到某个会话名下。

    由前端在 tool_end 时调用，是「产物归属任务」的写入端。
    """
    from urllib.parse import urlparse
    import json as _json

    path = getattr(self, "path", "") or ""

    # POST /new_conversation —— 同步开一个全新独立对话。
    # 为什么需要它：/command 是**异步**的（立即回 accepted，结果走 SSE），前端点
    # 「新建对话」拿不到真实结果，只能盲猜 + 定时刷新，用户看到的就是「点了没反应 /
    # 上下文没换」。这里同步调用 start_new_conversation（内部会 navigate_to_chat
    # 真正在平台网页开一个新对话页），完成后才回包，前端就能确定地刷新界面。
    if urlparse(path).path == "/new_conversation":
        try:
            ok, msg = _sync_new_conversation(main_module)
            self._send_json(200 if ok else 500,
                            {"type": "ok" if ok else "error",
                             "text": msg})
        except Exception as e:
            try:
                self._send_json(500, {"type": "error", "text": str(e)})
            except Exception:
                pass
        return

    if urlparse(path).path == "/register_artifacts":
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = _json.loads(raw.decode("utf-8") or "{}")
            cid = payload.get("conversation_id")
            paths = payload.get("paths") or []
            record_artifacts(cid, paths)
            self._send_json(200, {"type": "ok", "conversation_id": cid,
                                  "count": len(paths)})
        except Exception as e:
            try:
                self._send_json(500, {"type": "error", "text": str(e)})
            except Exception:
                pass
        return

    # POST /session_cmd —— 会话管理（新建/切换/删除会话）走本地，不进对话。
    # 旧实现把 '@@@@{"tool":"session_manager",...}@@@@' 当 command 发到 /command，
    # 结果这串内部协议会作为一条消息发给模型、还显示在对话区。用户明确要求
    # GUI 的点击必须由软件本地即时处理，不要变成发给 AI 的指令。
    if urlparse(path).path == "/session_cmd":
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = _json.loads(raw.decode("utf-8") or "{}")
            ok, msg = _sync_session_cmd(main_module, payload)
            self._send_json(200 if ok else 500,
                            {"type": "ok" if ok else "error", "text": msg})
        except Exception as e:
            try:
                self._send_json(500, {"type": "error", "text": str(e)})
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
        if getattr(handler.do_GET, "_xrz_patched", False):
            _INSTALLED = True
            return True

        try:
            orig = handler.do_GET

            def _new_do_get(self):
                _patched_do_GET(self, main_module, orig, app_dir)

            _new_do_get._xrz_patched = True
            _new_do_get.__name__ = "do_GET"
            handler.do_GET = _new_do_get

            # POST /register_artifacts（产物归属登记）
            orig_post = getattr(handler, "do_POST", None)
            if orig_post is not None and not getattr(orig_post, "_xrz_patched", False):
                def _new_do_post(self):
                    _patched_do_POST(self, main_module, orig_post, app_dir)

                _new_do_post._xrz_patched = True
                _new_do_post.__name__ = "do_POST"
                handler.do_POST = _new_do_post

            handler._xrz_scan_roots = _scan_roots  # 供诊断/测试引用
            handler._xrz_record_artifacts = record_artifacts
            handler._xrz_rebuild_registry = rebuild_registry_from_disk
            _INSTALLED = True
            try:
                marker = os.path.join(app_dir, "_files_listing_fix_installed.txt")
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("installed_at=%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                print("[XRZ-Files] 已安装补丁（/files 按会话过滤 + /register_artifacts 登记产物）",
                      flush=True)
            except Exception as ex:
                print("[XRZ-Files] 补丁已生效但写标记失败:", ex, flush=True)
            return True
        except Exception as e:
            print("[XRZ-Files] 补丁安装异常（将重试）:", e, flush=True)
            return False


def install(main_module, app_dir, timeout_s=180):
    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if _do_patch_once(main_module, app_dir):
                    # 补丁生效后，顺手做一次「产物索引数据修复」：
                    # registry 为空说明索引丢了，从磁盘回填，否则历史任务的
                    # 产物在 GUI 里永远显示不出来。
                    try:
                        n, msg = rebuild_registry_from_disk()
                        print("[XRZ-Files] registry 自检：%s" % msg, flush=True)
                    except Exception as e:
                        print("[XRZ-Files] registry 自检失败（不影响后端）:", e,
                              flush=True)
                    return
            except Exception as e:
                print("[XRZ-Files] 安装线程异常:", e, flush=True)
            time.sleep(0.2)
        print("[XRZ-Files] 安装超时，补丁未生效", flush=True)

    t = threading.Thread(target=_worker, daemon=True, name="xrz-files-listing-fix")
    t.start()
    return t
