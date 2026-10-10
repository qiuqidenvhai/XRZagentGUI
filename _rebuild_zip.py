# -*- coding: utf-8 -*-
"""重打可迁移版 zip：dist/XianRenZhangAgent -> 项目内 zip（校验通过后由调用方拷到桌面）"""
import ast
import io
import os, sys, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 【2026-10-06 修"写死项目路径"】原来写死 D:\软件\XianRenZhangAgent，
# 换机器/换盘符就找不到 dist。改为按本脚本位置动态定位。
SRC = os.path.join(HERE, "dist", "XianRenZhangAgent")
# 【2026-10-05 修"zip 根本没落盘"】之前直接往【桌面】写 1.3GB zip，沙箱对桌面大文件
# 写入会静默失败/中途被杀 —— 脚本却打印"DONE ... 1.278GB"（读到的是上一轮遗留的旧
# 文件大小），用户拿到桌面上根本不存在 zip。改为：先在项目内打包（沙箱可写），
# 校验通过后再由调用方拷到桌面。
DST = os.path.join(HERE, "XianRenZhangAgent_可迁移版_2026.10.06.zip")


def _code_only(src):
    """剔除注释与字符串字面量，只留真正的代码行（行号集合）。

    打包断言用它做"已删除某 API"这类检查 —— 否则注释/文档里提到那个名字
    （例如"startSystemMove 对无边框窗口无效"）会造成假失败。
    """
    lines = set()
    try:
        for tok in ast.walk(ast.parse(src)):
            if isinstance(tok, ast.stmt):
                lines.add(tok.lineno)
    except SyntaxError:
        for i, l in enumerate(src.split("\n"), 1):
            if not l.strip().startswith("#"):
                lines.add(i)
    return "\n".join(l for i, l in enumerate(src.split("\n"), 1) if i in lines)


t0 = time.time()
n = 0
with zipfile.ZipFile(DST, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
    for root, dirs, files in os.walk(SRC):
        dirs.sort(); files.sort()
        for f in files:
            p = os.path.join(root, f)
            arc = os.path.relpath(p, os.path.dirname(SRC))
            try:
                z.write(p, arc)
                n += 1
                if n % 3000 == 0:
                    print("  %d files, %.0fs" % (n, time.time()-t0), flush=True)
            except Exception as e:
                print("  SKIP %s: %r" % (p, e), flush=True)
sz = os.path.getsize(DST) if os.path.exists(DST) else 0
print("DONE %d entries, %.3f GB, %.0fs" % (n, sz/1e9, time.time()-t0), flush=True)

# 【真实验证】zip 必须真实存在 + 条目数与写入数一致 + 全部 CRC 正确。
# 旧实现只 print getsize()（读到上轮遗留旧文件的大小）就报 DONE，导致用户拿到
# 的桌面上根本没有 zip。这里不通过就直接抛错，绝不假成功。
if not os.path.exists(DST):
    raise RuntimeError("ZIP 未落盘: %s" % DST)
if sz < 100 * 1e6:
    raise RuntimeError("ZIP 体积异常偏小(%.3f GB)，疑似写入失败" % (sz / 1e9))
with zipfile.ZipFile(DST) as _zc:
    _names = _zc.namelist()
    if len(_names) != n:
        raise RuntimeError("ZIP 条目数不符: 写入 %d, 实际 %d" % (n, len(_names)))
    _bad = _zc.testzip()
    if _bad is not None:
        raise RuntimeError("ZIP CRC 校验失败: %s" % _bad)
print("VERIFY: zip 真实落盘 %.3f GB / %d 条目 / CRC 全部正确" % (sz/1e9, n), flush=True)

# 校验关键修复文件在包内且含标记
with zipfile.ZipFile(DST) as z:
    data = z.read("XianRenZhangAgent/_files_listing_fix.py").decode("utf-8", "replace")
    assert "_resolve_task_style_ids" in data, "映射修复缺失!"
    print("VERIFY: zip 内 _files_listing_fix.py 含 _resolve_task_style_ids (映射修复已入包)")
    # 【2026-10-06】删对话只删元信息（网址/平台/历史上下文），产物文件必须保留
    assert "_delete_conversation_meta" in data, "删除对话元信息逻辑缺失!"
    assert 'parsed.path.startswith("/conversations")' in data, \
        "DELETE /conversations 未被拦截 —— 会落到删文件分支报 403!"
    assert "产物文件全部保留" in data, "删对话的产物保护说明缺失!"
    assert "TASKS_INDEX_PATH" in data, "删对话未清理 tasks.json 索引!"
    assert "CONVERSATIONS_DIR" in data, "删对话未清理历史上下文 JSON!"
    print("VERIFY: zip 内 删对话=只删元信息(索引+历史上下文)，产物文件全部保留")
    ppt = z.read("XianRenZhangAgent/agent_core/pptx_builder.py").decode("utf-8", "replace")
    assert "_cover_write[_main_slot" in ppt and "cap>=20" in ppt, "PPT 模板分配修复缺失!"
    print("VERIFY: zip 内 pptx_builder.py 含 封面/大字槽分配修复 (PPT 挤字根治已入包)")
    bp = z.read("XianRenZhangAgent/agent_core/templates/pptx_templates/scripts/build_pptx.py").decode("utf-8", "replace")
    assert "if not (tf.text or \"\").strip():" in bp, "autofit 判定修复缺失!"
    print("VERIFY: zip 内 build_pptx.py 含 autofit 全文判定修复 (已入包)")
    gui = z.read("XianRenZhangAgent/gui.html").decode("utf-8", "replace")
    assert "nearBottom" in gui, "thinking 卡顿修复(增量追加+贴底滚)缺失!"
    assert "cleaned.startsWith(cur)" in gui, "thinking 增量追加修复缺失!"
    assert "classList.remove('collapsed')" in gui and "fileSidebarToggle" in gui, "预览自动展开修复缺失!"
    assert gui.count("flex-shrink: 0") >= 16, "thinking 块坍缩修复(flex-shrink:0)缺失!"
    # 【2026-10-06】删除会话：按钮常驻可见 + 当前会话也可删 + 删后回退初始页
    assert "opacity:.55" in gui, "删除会话按钮不可见(opacity:0 回归)!"
    assert "当前会话不可删除" not in gui, \
        "不该禁止删除当前会话 —— 用户要求删掉后回退初始页面即可!"
    assert 'onclick="event.stopPropagation(); deleteSession(${s.id})"' in gui, \
        "删除会话按钮未绑定 deleteSession!"
    assert "resetChatView()" in gui, "删当前会话后未回退初始页面!"
    print("VERIFY: zip 内 gui.html 含 thinking 卡顿 + 预览自动展开 + 坍缩修复 (flex-shrink:0 已入包)")
    print("VERIFY: zip 内 gui.html 删除会话按钮常驻可见 + 当前会话可删 + 删后回退初始页")
    da = z.read("XianRenZhangAgent/desktop_app.py").decode("utf-8", "replace")
    assert "def shutdown_backend_and_children" in da, "关 GUI 连带清理修复缺失!"
    assert "taskkill" in da and "/T" in da, "关 GUI 连带清理(taskkill 进程树)缺失!"
    assert "_cleanup_on_close" in da, "closeEvent 联动清理缺失!"
    # 【2026-10-06】四边四角缩放 + Aero Snap
    assert "class _ResizeHotZone" in da, "缩放热区控件缺失!"
    assert "startSystemResize" in da, "startSystemResize(系统缩放)缺失!"
    assert "_install_resize_hotzones" in da, "热区安装逻辑缺失!"
    assert "_layout_hotzones" in da, "热区随窗口重排逻辑缺失!"
    assert "mouseDoubleClickEvent" in da, "标题栏双击最大化缺失!"
    for _z in ('"top"', '"bottom"', '"left"', '"right"', '"tl"', '"tr"', '"bl"', '"br"'):
        assert _z in da, "热区缺少 %s!" % _z
    # Aero Snap（自实现，startSystemResize/startSystemMove 都不提供分屏）
    assert "def _compute_snap_layout" in da, "Aero Snap 边缘检测缺失!"
    assert "def _snap_geometry" in da, "Aero Snap 布局几何缺失!"
    assert "class _SnapOverlay" in da, "Aero Snap 预览框缺失!"
    for _m in ("_snap_begin", "_snap_update", "_snap_commit", "_snap_clear_min",
               "_screen_area_at"):
        assert "def %s" % _m in da, "Aero Snap 方法 %s 缺失!" % _m
    assert "setMinimumSize(0, 0)" in da and "_snap_min_saved" in da, \
        "分屏最小尺寸放开逻辑缺失!"
    assert "startSystemMove" not in _code_only(da), \
        "startSystemMove 对无边框窗口不触发 Aero Snap，应已移除!"
    # 只查代码行，且必须精确匹配函数定义 —— 直接搜 "_hit_test" 会误报
    # _hit_test_enabled（仍在使用的开关属性），已踩过。
    _dacode = _code_only(da)
    assert "def _hit_test(" not in _dacode, "废弃的 WM_NCHITTEST 方案残留( def _hit_test )!"
    assert "startSystemMove" not in _dacode, \
        "startSystemMove 对无边框窗口不触发 Aero Snap，应已移除!"
    # 最小尺寸单一事实来源（不能用 self.minimumSize() 当原值，会被布局污染）
    assert "def _min_win_size()" in da and "self._snap_min_saved = _min_win_size()" in da, \
        "最小尺寸常量修复缺失!"
    assert "setMinimumSize(0, 0)" in da and "_snap_min_pending" in da, \
        "分屏最小尺寸放开/推迟恢复逻辑缺失!"
    assert "_snap_restore_geo" in da, "拖出分屏恢复原始尺寸逻辑缺失!"
    print("VERIFY: zip 内 desktop_app.py 含 四边四角缩放热区 + 自实现 Aero Snap(边缘检测/预览/应用布局)")
    print("VERIFY: zip 内 desktop_app.py 含 关GUI连带清理 (taskkill 后端+浏览器+子代理 已入包)")

    # ---- 【2026-10-06 新增】"绝不写死开发者桌面/项目路径" 红线校验 ----
    # 这条是给别人用的软件，写死 C:\Users\<某人>\Desktop 在别人电脑上必然失效。
    _FORBIDDEN = ("X.LAPTOP-CA1GJQE3", r"D:\软件\XianRenZhangAgent",
                  "D:/软件/XianRenZhangAgent", "/d/软件/XianRenZhangAgent")
    _PRODUCT = [
        "XianRenZhangAgent/gui.html", "XianRenZhangAgent/desktop_app.py",
        "XianRenZhangAgent/agent_core/commander.py",
        "XianRenZhangAgent/agent_core/user_paths.py",
        "XianRenZhangAgent/agent_core/session.py",
        "XianRenZhangAgent/agent_core/browser.py",
        "XianRenZhangAgent/agent_core/platform_browser.py",
        "XianRenZhangAgent/agent_core/subagent_manager.py",
        "XianRenZhangAgent/agent_core/pptx_builder.py",
        "XianRenZhangAgent/运行测试.bat", "XianRenZhangAgent/启动仙人掌.bat",
        "XianRenZhangAgent/make_shortcut.py",
    ]
    _bad = []
    for _p in _PRODUCT:
        try:
            _txt = z.read(_p).decode("utf-8", "replace")
        except KeyError:
            continue
        # 用 tokenize 精确区分"代码"与"注释/docstring 字符串"，
        # 字符串字面量里的路径（如模块说明中的示例）不算写死。
        import io as _io
        import tokenize as _tok
        _code_lines = set()
        try:
            for _t in _tok.generate_tokens(_io.StringIO(_txt).readline):
                if _t.type in (_tok.NAME, _tok.OP, _tok.NUMBER):
                    _code_lines.add(_t.start[0])
                elif _t.type == _tok.STRING and _t.line.strip().startswith(
                        ("r\"", "r'", "#", '"""', "'''")):
                    pass
        except (_tok.TokenError, IndentationError, SyntaxError):
            # 解析失败则退回"整行非注释即算代码"的粗判
            for _i, _l in enumerate(_txt.split("\n"), 1):
                _st = _l.strip()
                if not _st.startswith(("#", '"', "*", "'")):
                    _code_lines.add(_i)
        for _i, _l in enumerate(_txt.split("\n"), 1):
            _st = _l.strip()
            # 跳过注释 / 任何字符串字面量所在行（docstring 里的示例路径 OK）
            if _st.startswith(("#", '"', "*", "'", "r\"", "r'")):
                continue
            if _i not in _code_lines:
                continue
            for _pat in _FORBIDDEN:
                if _pat in _l:
                    _bad.append("%s:%d %s" % (_p, _i, _st[:90]))
                    break
    assert not _bad, "包内仍有写死的开发者路径!\n  " + "\n  ".join(_bad[:12])
    print("VERIFY: 包内产品代码 0 处写死开发者桌面/项目路径 (换机器也能跑)")
    # 顺带确认统一解析模块在包里
    up = z.read("XianRenZhangAgent/agent_core/user_paths.py").decode("utf-8", "replace")
    assert "def desktop_dir" in up and "User Shell Folders" in up, "user_paths 桌面解析模块缺失!"
    cmd = z.read("XianRenZhangAgent/agent_core/commander.py").decode("utf-8", "replace")
    assert "user_paths import desktop_dir" in cmd, "commander 未接上动态桌面解析!"
    print("VERIFY: zip 内 user_paths.py + commander 动态桌面解析 已入包")
    # 【2026-10-10】子代理空参保护：query/url 为空时不派子代理（否则子代理
    # 自己瞎猜主题、白烧 token 还产出无用报告 —— 实测 DeepSeek 会漏传 query）
    assert "browser_research 缺少 query 参数" in cmd, "browser_research 空参保护缺失!"
    assert "browser_visit 缺少 url 参数" in cmd, "browser_visit 空参保护缺失!"
    # 子代理事件转发器（子代理卡能显示的唯一依赖，必须两处都在）
    assert cmd.count("set_event_forwarder(self._on_event)") >= 2, \
        "子代理事件转发器缺失（子代理卡不会显示）!"
    print("VERIFY: zip 内 子代理空参保护 + 事件转发器（子代理卡可显示）已入包")
    flf = z.read("XianRenZhangAgent/_files_listing_fix.py").decode("utf-8", "replace")
    assert "时间最接近" in flf, "产物按会话精确映射修复缺失!"
    assert "最近 N 个任务" in flf or "绝不再并" in flf, "去掉『最近N个任务』宽泛回退标记缺失!"
    assert 'startswith("task_")' in flf and "_current_conversation_id()" in flf, "register_artifacts 真会话兜底缺失!"
    print("VERIFY: zip 内 _files_listing_fix.py 含 产物栏按会话隔离 + 新产物登记兜底 (已入包)")
    g2 = z.read("XianRenZhangAgent/gui.html").decode("utf-8", "replace")
    assert "deleteFile" not in g2, "删除生成文件功能应已移除但仍在包内!"
    print("VERIFY: zip 内 gui.html 已移除删除生成文件功能 (无 deleteFile)")

# 完成标记（供分离进程轮询）
with open(r"D:\软件\XianRenZhangAgent\_zip_build_done.txt", "w", encoding="utf-8") as f:
    f.write("DONE %d entries %.3fGB %.0fs" % (n, sz/1e9, time.time()-t0))
