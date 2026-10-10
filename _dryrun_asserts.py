# -*- coding: utf-8 -*-
"""干跑 _rebuild_zip.py 的所有断言（不真打包），先确认校验逻辑能过。"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "XianRenZhangAgent")
fails = []
oks = []


def chk(label, cond, detail=""):
    (oks if cond else fails).append("%s %s %s" % ("[PASS]" if cond else "[FAIL]", label, detail))


def rd(rel):
    p = os.path.join(DIST, rel)
    if not os.path.isfile(p):
        return None
    return io.open(p, encoding="utf-8").read()


def _code_only(src):
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


da = rd("desktop_app.py")
gui = rd("gui.html")
cm = rd("agent_core/commander.py")
up = rd("agent_core/user_paths.py")
bat = rd("运行测试.bat")

# desktop_app 缩放 + Aero Snap
chk("class _ResizeHotZone", "_ResizeHotZone" in da)
chk("startSystemResize", "startSystemResize" in da)
chk("_install_resize_hotzones", "_install_resize_hotzones" in da)
chk("_layout_hotzones", "_layout_hotzones" in da)
chk("mouseDoubleClickEvent", "mouseDoubleClickEvent" in da)
for z in ('"top"', '"bottom"', '"left"', '"right"', '"tl"', '"tr"', '"bl"', '"br"'):
    chk("zone %s" % z, z in da)
chk("_compute_snap_layout", "def _compute_snap_layout" in da)
chk("_snap_geometry", "def _snap_geometry" in da)
chk("class _SnapOverlay", "class _SnapOverlay" in da)
for m in ("_snap_begin", "_snap_update", "_snap_commit", "_snap_clear_min",
          "_screen_area_at"):
    chk("method %s" % m, "def %s" % m in da)
chk("setMinimumSize(0, 0)", "setMinimumSize(0, 0)" in da)
chk("_snap_min_saved", "_snap_min_saved" in da)
chk("startSystemMove 已删(代码层)", "startSystemMove" not in _code_only(da))
# 精确匹配函数定义 —— "_hit_test_enabled" 是仍在使用的开关属性，
# 直接搜子串 "_hit_test" 会误报（已踩两次）
chk("_hit_test 函数已删(代码层)", "def _hit_test(" not in _code_only(da))
chk("_min_win_size()", "def _min_win_size()" in da)
chk("_snap_min_saved = _min_win_size()", "self._snap_min_saved = _min_win_size()" in da)
chk("_snap_min_pending", "_snap_min_pending" in da)
chk("_snap_restore_geo", "_snap_restore_geo" in da)
chk("shutdown_backend_and_children", "def shutdown_backend_and_children" in da)
chk("taskkill", "taskkill" in da)
chk("_cleanup_on_close", "_cleanup_on_close" in da)

# 删除会话：按钮常驻可见 + 当前会话【也可删】，删后回退初始页
# 注意 _isCur 仍保留（用于标 ● 和 title 文案），不能用它判断"禁删"。
chk("si-delete 常驻可见(opacity:.55)", "opacity:.55" in gui)
chk("当前会话也可删(无禁删限制)", "当前会话不可删除" not in gui)
chk("所有会话都绑 deleteSession",
    'onclick="event.stopPropagation(); deleteSession(${s.id})"' in gui)
chk("删当前会话后回退初始页", "resetChatView()" in gui)

# gui.html 既有修复
chk("gui flex-shrink", gui.count("flex-shrink: 0") >= 16)
chk("gui nearBottom", "nearBottom" in gui)
chk("gui 预览自动展开", "fileSidebarToggle" in gui)

# 路径动态化
chk("user_paths.desktop_dir", "def desktop_dir" in up)
chk("user_paths 注册表", "User Shell Folders" in up)
chk("commander 动态桌面", "user_paths import desktop_dir" in cm)
chk("commander 无写死用户名(代码层)", "X.LAPTOP-CA1GJQE3" not in _code_only(cm))
chk("bat 动态桌面", "DESKTOP_DIR" in bat)
chk("bat 包内 runtime", "runtime" in bat)

# 子代理卡与空参保护（2026-10-10）
chk("research 空参保护", "browser_research 缺少 query 参数" in cm)
chk("visit 空参保护", "browser_visit 缺少 url 参数" in cm)
chk("事件转发器两处都在", cm.count("set_event_forwarder(self._on_event)") >= 2,
    cm.count("set_event_forwarder(self._on_event)"))
chk("upsertSubagentCard 在前端", "upsertSubagentCard" in gui)
chk("handleGuiEvent 判 subagent_task_id", "subagent_task_id" in gui)

out = oks + fails
out.append("")
out.append("PASS %d / FAIL %d" % (len(oks), len(fails)))
io.open(os.path.join(ROOT, "_dryrun_out.txt"), "w", encoding="utf-8").write("\n".join(out))
print("PASS %d / FAIL %d" % (len(oks), len(fails)))
for f in fails:
    print("  " + f)
