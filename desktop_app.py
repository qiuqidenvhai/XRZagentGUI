"""
仙人掌 Agent —— 原生桌面应用壳（PySide6）。

设计目标（来自用户反复强调的诉求）：
  - 像"独立软件"，不要浏览器外壳（无地址栏、无书签，就是个本地 App 窗口）
  - 一键双击启动：自动拉起后端（terminal.pyc），等待就绪后再渲染 GUI
  - 关闭窗口只是关面板，agent 在后台继续跑（除非显式退出）
  - 深色 / 浅色主题同步到原生窗口边框
  - 任务栏显示仙人掌图标（AppUserModelID + 窗口图标）
"""

import os
import sys
import time
import subprocess
import urllib.request
import ctypes
import ctypes.wintypes

from PySide6.QtCore import Qt, QSize, QUrl, QTimer, QObject, Slot, QEvent, QPoint, QRect
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QScreen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSizeGrip, QSpacerItem, QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage

from pathlib import Path
from agent_core.xrz_paths import PLAYWRIGHT_BROWSERS_PATH, DATA_ROOT

# ── 路径 ──
path = os.path
dirname = os.path.dirname
abspath = os.path.abspath
__file__ = abspath(__file__)
APP_DIR = dirname(abspath(__file__))
TERMINAL_PY = "terminal.py"           # CORS 增强包装，内部再 exec terminal.pyc（源码已丢失）
ICON_PNG = os.path.join(APP_DIR, "__xianrenzhang_icon.png")
ICON_ICO = os.path.join(APP_DIR, "__xianrenzhang_icon.ico")

# ── 无边框窗口的四边缩放 + Aero Snap（2026-10-06）────────────────────────
# 背景：本窗口用 Qt.FramelessWindowHint 去掉了系统边框，Qt 自带的边缘 resize
# 因此失效，之前只能在右下角拖一个 QSizeGrip。用户要求"四条边都能拖动缩放，
# 还要 Win11 贴边自动分屏"。
# 做法：在 nativeEvent 里处理 WM_NCHITTEST，按鼠标位置返回 HTLEFT/HTRIGHT/
# HTTOP/HTTOPLEFT/... ，把缩放交还给 Windows 原生处理。这样：
#   ① 四边 + 四角都能拖动缩放（原生手感、带系统动画）；
#   ② Aero Snap（贴左/右半屏、贴角 1/4 屏、拖到顶端最大化）自动获得 ——
#      它本来就是 Windows 对"可缩放窗口"提供的系统能力，不用自己实现。
# 注意：标题栏区域必须返回 HTCAPTION 才能拖动窗口、双击最大化、贴边分屏。
WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTCAPTION = 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

# 边缘感应宽度（像素）：比系统默认 4px 略宽，好抓
_RESIZE_MARGIN = 6
# 标题栏高度（像素），与 TitleBar.setFixedHeight(38) 保持一致
_CAPTION_HEIGHT = 38
# Aero Snap 判定：鼠标进入屏幕边缘这个距离内就亮起分屏预览（像素）
_SNAP_EDGE = 12
# 窗口正常状态下的最小尺寸。
# 【为什么必须用常量而不是 self.minimumSize()】QMainWindow 布局的
# minimumSizeHint 会把 minimumSize 覆盖成一个很小的值（实测读回 [235,38]，
# 就是标题栏+侧栏的 hint）。若拿它当"原值"保存，分屏取消时装回 235x38，
# 等于最小尺寸根本没恢复，窗口能被缩到 400x300（用户会发现窗口能缩到
# 比设计值小得多）。所以这里定义单一事实来源，恢复时直接用它。
_MIN_W, _MIN_H = 900, 620


def _min_win_size():
    """窗口正常最小尺寸（QSize）。每次新建，避免共享可变对象。"""
    return QSize(_MIN_W, _MIN_H)



# 【2026-10-06 说明】这里曾经有一个 _hit_test() 纯函数 + HT* 常量，用于在
# nativeEvent 里接管 WM_NCHITTEST 实现边缘缩放。**该方案在 Qt 无边框窗口上
# 完全无效**（真机实测所有探测点返回 0/1，四边四角拖不动），已废弃：
#   缩放  → 改用透明热区 + QWindow.startSystemResize(Qt.Edge)（见 _ResizeHotZone）
#   分屏  → 改用自绘预览（见下方 _compute_snap_layout / _SnapOverlay）
# 相关常量（WM_NCHITTEST / HT* / _hit_test）已一并删除，避免后人误用。


def _compute_snap_layout(cursor_x, cursor_y, area):
    """判断鼠标停在屏幕哪个分屏热区，返回布局 key（不在热区则 None）。

    对齐 Win11 原生 Aero Snap 的规则：
      · 贴左/右边  → 半屏（left / right）
      · 贴上边    → 最大化（max）
      · 贴左/上   → 左上 1/4（tl）
      · 贴右/上   → 右上 1/4（tr）
      · 贴左/下   → 左下 1/4（bl）
      · 贴右/下   → 右下 1/4（br）
    角判定优先于边（先判是否同时贴两个方向）。
    纯函数，便于单测。
    """
    if not area:
        return None
    ax, ay, aw, ah = area
    e = _SNAP_EDGE
    near_l = cursor_x <= ax + e
    near_r = cursor_x >= ax + aw - e
    near_t = cursor_y <= ay + e
    near_b = cursor_y >= ay + ah - e
    # 角落：先判上下，再判左右
    if near_t and near_l:
        return "tl"
    if near_t and near_r:
        return "tr"
    if near_b and near_l:
        return "bl"
    if near_b and near_r:
        return "br"
    if near_l:
        return "left"
    if near_r:
        return "right"
    if near_t:
        return "max"
    return None


def _snap_geometry(layout, area):
    """给布局 key 算出目标几何 (x, y, w, h)。纯函数，便于单测。"""
    ax, ay, aw, ah = area
    if layout == "left":
        return ax, ay, aw // 2, ah
    if layout == "right":
        return ax + aw // 2, ay, aw - aw // 2, ah
    if layout == "max":
        return ax, ay, aw, ah
    if layout == "tl":
        return ax, ay, aw // 2, ah // 2
    if layout == "tr":
        return ax + aw // 2, ay, aw - aw // 2, ah // 2
    if layout == "bl":
        return ax, ay + ah // 2, aw // 2, ah - ah // 2
    if layout == "br":
        return ax + aw // 2, ay + ah // 2, aw - aw // 2, ah - ah // 2
    return None


class _SnapOverlay(QWidget):
    """Aero Snap 布局预览框：拖动窗口贴边时，在【整个屏幕】上画半透明色块，
    提示松手后会摆到哪个位置（半屏 / 四分之一屏 / 最大化）。

    必须是**独立顶层窗口**（Qt.Tool + FramelessWindowHint + StaysOnTopHint），
    不能画在主窗口里 —— 因为主窗口此时被拖到屏幕边缘，预览要覆盖的是
    屏幕的另一半，画在窗口内会被窗口边界裁掉。
    """

    def __init__(self, app):
        super().__init__(None)
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 不挡鼠标
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)       # 弹出时不抢焦点
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            "background: rgba(0, 120, 215, 80);"
            "border: 2px solid rgba(0, 160, 255, 220);"
            "border-radius: 8px;")
        self.hide()
        self._app = app

    def show_layout(self, geo):
        self.setGeometry(int(geo[0]), int(geo[1]), int(geo[2]), int(geo[3]))
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()


def _pick_icon_ico():
    """Prefer a content-hashed icon (__xianrenzhang_icon_<md5>.ico) when present.

    Windows caches Qt/window icons per path; a rebuilt .ico at the same path can
    keep showing the stale image. Picking the newest hashed file forces a reload.
    """
    try:
        import glob as _glob
        cands = sorted(
            _glob.glob(os.path.join(APP_DIR, "__xianrenzhang_icon_*.ico")),
            key=os.path.getmtime,
            reverse=True,
        )
        for c in cands:
            if os.path.exists(c):
                return c
    except Exception:
        pass
    return ICON_ICO


ICON_PATH = _pick_icon_ico() if os.path.exists(_pick_icon_ico()) else ICON_PNG
PORT = 8888
BACKEND_URL = f"http://127.0.0.1:{PORT}"


def log(*a):
    print("[仙人掌壳]", *a, flush=True)


def _set_appusermodel_id():
    """让 Windows 任务栏把本进程归到同一个 App（仙人掌）分组，图标稳定显示。"""
    try:
        import ctypes
        from ctypes import wintypes
        appid = "XianRenZhang.Agent.DesktopApp"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    except Exception as e:
        log("设置 AppUserModelID 失败（不影响功能）:", e)


def _apply_win_class_icon(win):
    """【真机修复 2026-09-21】把仙人掌图标同时装到【窗口类】上。

    问题现象：任务栏 / Alt+Tab 里本程序的按钮是一个系统默认的「空白窗口」图标，
    用户原话「是空的程序的图标」。

    根因（实测取值验证过）：
      · 只调用 Qt 的 setWindowIcon() → 等价于 WM_SETICON，
        实测 WM_GETICON 的 ICON_SMALL / ICON_BIG 确实是仙人掌 ✓
      · 但窗口【类】图标 GCLP_HICON / GCLP_HICONSM 仍是系统给的通用
        「空白窗口」图标（句柄 65579，系统共享图标）。
        Windows 的任务栏按钮取的是【类图标】，所以显示为空白图标 ✗

    修复：用 SetClassLongPtrW 把 GCLP_HICON / GCLP_HICONSM 也换成仙人掌。
    必须在窗口创建（拿到原生 HWND）之后调用。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(win.winId())
        path = ICON_PATH if os.path.exists(ICON_PATH) else ICON_PNG
        if not os.path.exists(path):
            return False

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x10
        GCLP_HICON = -14
        GCLP_HICONSM = -34

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetClassLongPtrW.restype = ctypes.c_void_p
        user32.SetClassLongPtrW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ]

        big = user32.LoadImageW(None, path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        sm = user32.LoadImageW(None, path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        ok = False
        if big:
            user32.SetClassLongPtrW(hwnd, GCLP_HICON, big)
            ok = True
        if sm:
            user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, sm)
            ok = True
        # 顺带把窗口图标再刷一遍（部分 Windows 版本要 WM_SETICON + 类图标一起才生效）
        WM_SETICON = 0x0080
        ICON_BIG, ICON_SMALL = 1, 0
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        if sm:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, sm)
        log("已设置窗口类图标:", ("big" if big else "") + ("+small" if sm else ""))
        return ok
    except Exception as e:
        log("设置窗口类图标失败（不影响功能）:", e)
        return False


def _detect_pythonw():
    """找到带 playwright + PySide6 的 pythonw.exe（无黑窗）。

    【#86 可迁移 2026-09-24】优先用「软件目录内 runtime\\pythonw.exe」
    （PyInstaller / 便携目录自带的解释器），这样整个软件文件夹拷到任意盘符
    / 任意电脑都能直接跑，绝不依赖 D:\\软件\\Python 这种写死路径。
    """
    here = dirname(abspath(__file__))
    candidates = [
        # 1) 便携目录自带解释器（最高优先级，保证可迁移）
        os.path.join(here, "runtime", "pythonw.exe"),
        os.path.join(here, "runtime", "python.exe"),
        os.path.join(here, "pythonw.exe"),
        # 2) 开发环境常见安装路径（回退）
        r"D:\软件\Python\pythonw.exe",
        r"D:\软件\Python\python.exe",
        # 3) 当前解释器
        sys.executable,
    ]
    for c in candidates:
        if c and os.path.exists(c):
            # 验证依赖齐备
            try:
                r = subprocess.run(
                    [c, "-c", "import playwright, PySide6"],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode == 0:
                    return c
            except Exception:
                pass
    return sys.executable


def backend_healthy():
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_backend_pids(port=PORT):
    """列出监听 port 的所有后端 PID（pythonw/python 可能多实例）。找不到则空。"""
    try:
        import psutil
        pids = set()
        for c in psutil.net_connections(kind="tcp"):
            la = getattr(c, "laddr", None)
            if la and len(la) >= 2 and la[1] == port and c.status == "LISTEN":
                pid = getattr(c, "pid", 0)
                if pid and pid > 0:
                    pids.add(pid)
        return sorted(pids)
    except Exception:
        return []


def _kill_leftover_browsers():
    """【二次清扫】按 cmdline 特征清掉 taskkill /T 可能漏掉的 XRZ 专属浏览器残留
    （playwright node driver + xrz_data/playwright_browsers 下的 chrome renderer/gpu）。
    只匹配含 'playwright_browsers' 的 chrome / 含 playwright+node.exe 的进程，
    绝不碰用户系统 Chrome（路径不含 xrz_data/playwright_browsers）。"""
    try:
        import psutil
    except Exception:
        return 0
    n = 0
    me = os.getpid()
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            pid = p.info.get("pid")
            if not pid or pid == me:
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            nm = p.info.get("name") or ""
            is_xrz_chrome = nm in ("chrome.exe", "chromium.exe") and "playwright_browsers" in cl
            is_xrz_node = "node.exe" in nm and "playwright" in cl
            if not (is_xrz_chrome or is_xrz_node):
                continue
            p.kill()
            n += 1
        except Exception:
            pass
    return n


def shutdown_backend_and_children(dry_run=False):
    """【2026-09-26 修复"关 GUI 没关连带进程"】关 GUI 时连带清理：
    后端 8888 整棵进程树（含 playwright node driver + XRZ 专属 chrome + 子代理）。

    后端**没有**可远程调用的优雅关闭端点（/exit、/quit、/shutdown 均 404），
    故直接 taskkill /T /F 杀整棵 8888 监听进程树（/T 带出它派生的 node driver、
    chrome 主进程、子代理；DETACHED_PROCESS 只脱离控制台、不影响父子树，仍杀得到）。
    随后二次清扫残留 chrome renderer / gpu（/T 偶有漏网）。

    安全：登录态是磁盘文件（cookies.json + browser_profiles/），强杀进程不丢，
    重启后端会从磁盘 reload。taskkill 用 subprocess 列表直接调 taskkill.exe，
    不经 MSYS shell，旗标 /T /F 不会被 Git Bash 吞。

    dry_run=True 只列 8888 后端 PID 及其递归子进程，不执行任何 kill。
    """
    import time as _time
    port = PORT
    found = find_backend_pids(port)
    log(("[dry-run] 8888 后端 PID: " if dry_run else "发现后端 PID: ") + repr(found))

    if dry_run:
        try:
            import psutil
            for pid in found:
                try:
                    p = psutil.Process(pid)
                    kids = [(c.pid, c.name()) for c in p.children(recursive=True)]
                    log("[dry-run] pid=%d %s 子进程数=%d" % (pid, p.name(), len(kids)))
                    for cp, cn in kids[:30]:
                        log("[dry-run]   child pid=%d %s" % (cp, cn))
                except Exception as e:
                    log("[dry-run]   pid %d 详情失败: %r" % (pid, e))
        except Exception:
            pass
        return found

    # ① 杀 8888 后端整棵进程树（带出 node driver + chrome 主进程 + 子代理）
    for pid in found:
        if pid == os.getpid():
            continue
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=15,
            )
            log("taskkill /T /F 后端进程树 PID=%d" % pid)
        except Exception as e:
            log("[WARN] taskkill PID=%d 失败: %r" % (pid, e))
    _time.sleep(2.0)

    # ② 二次清扫残留 chrome renderer / gpu（/T 偶有漏网）
    leftover = _kill_leftover_browsers()
    if leftover:
        log("二次清扫残留浏览器/driver %d 个" % leftover)

    # ③ 复查 8888 是否真的空了
    after = find_backend_pids(port)
    if after:
        log("[WARN] 仍有 8888 监听残留: %r（可能属其它实例，不强杀）" % after)
    else:
        log("已关闭全部连带进程（后端 + chromium 浏览器 + playwright driver + 子代理）")
    return found


class _ResizeHotZone(QWidget):
    """窗口边缘/四角的透明缩放热区（2026-10-06）。

    鼠标按下 → 调 Qt 官方的 startSystemResize(edges)，由系统的模态循环完成
    缩放，手感与原生窗口一致。
    悬停时把对应方向的光标显示出来，让用户知道这里能拖。

    【注意】startSystemResize **不提供 Aero Snap** —— 对无边框窗口，Windows
    认为它没有 frame，不触发贴边分屏（真机实测拖边到屏幕边缘无分屏）。
    分屏由另一套实现负责：标题栏拖动时的 _snap_update/_snap_commit
    （边缘检测 + _SnapOverlay 预览 + setGeometry 摆位）。
    """

    # zone -> (Qt.Edge 组合, 鼠标光标)
    ZONES = {
        "top":    (Qt.TopEdge, Qt.SizeVerCursor),
        "bottom": (Qt.BottomEdge, Qt.SizeVerCursor),
        "left":   (Qt.LeftEdge, Qt.SizeHorCursor),
        "right":  (Qt.RightEdge, Qt.SizeHorCursor),
        "tl": (Qt.TopEdge | Qt.LeftEdge, Qt.SizeFDiagCursor),
        "tr": (Qt.TopEdge | Qt.RightEdge, Qt.SizeBDiagCursor),
        "bl": (Qt.BottomEdge | Qt.LeftEdge, Qt.SizeBDiagCursor),
        "br": (Qt.BottomEdge | Qt.RightEdge, Qt.SizeFDiagCursor),
    }

    def __init__(self, win, zone):
        super().__init__(win)
        self.win_ref = win
        self.zone = zone
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setCursor(self.ZONES.get(zone, (None, Qt.ArrowCursor))[1])
        self.setToolTip("拖动调整窗口大小（贴边可分屏）")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            edges = self.ZONES.get(self.zone, (None, None))[0]
            w = self.win_ref
            # 用户开始手动拖边缩放 → 退出分屏状态，把最小尺寸恢复正常，
            # 否则窗口还锁在分屏时放开的 0x0，缩放手感会不对。
            # 同时清掉"分屏前原始几何"：用户既然手动调过大小，之后从分屏
            # 拖出来就该保持他自己调的大小，而不是被还原。
            try:
                w._snap_clear_min()
                w._snap_restore_geo = None
            except AttributeError:
                pass
            # 最大化/全屏时不该还能拖边框
            if edges and not (w.isMaximized() or w.isFullScreen()):
                try:
                    wh = w.windowHandle()
                    if wh is None:
                        # 窗口还没拿到原生句柄时，winId() 会强制创建
                        w.winId()
                        wh = w.windowHandle()
                    if wh is not None:
                        # 【核心】Qt 官方系统缩放：走系统模态循环，手感与原生一致，
                        # 且贴屏幕边缘/角会自动弹出 Aero Snap 分屏预览。
                        wh.startSystemResize(edges)
                        ev.accept()
                        return
                    log("[缩放] 拿不到 windowHandle，热区", self.zone, "本次不处理")
                except (AttributeError, RuntimeError) as e:
                    log("[缩放] startSystemResize 失败:", repr(e))
                    # 退化：返回 False 走下面 Qt 默认处理
        super().mousePressEvent(ev)


class TitleBar(QWidget):
    """无边框窗口的自定义标题栏：拖动 + 最小化/最大化/关闭。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(38)
        self.setObjectName("TitleBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(4)

        self.icon = QLabel()
        if os.path.exists(ICON_PNG):
            pm = QPixmap(ICON_PNG).scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.icon.setPixmap(pm)
        else:
            self.icon.setText("🌵")
        lay.addWidget(self.icon)

        self.title = QLabel("仙人掌 Agent")
        self.title.setObjectName("TitleLabel")
        lay.addWidget(self.title)
        lay.addItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.btn_min = self._make_btn("—", self._on_min)
        self.btn_max = self._make_btn("▢", self._on_max)
        self.btn_close = self._make_btn("✕", self._on_close, danger=True)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        lay.addWidget(self.btn_close)

        self._drag_pos = None

    def _make_btn(self, text, slot, danger=False):
        b = QPushButton(text)
        b.setFixedSize(34, 26)
        b.setObjectName("TitleBtnDanger" if danger else "TitleBtn")
        b.clicked.connect(slot)
        return b

    def _on_min(self):
        self.parent.showMinimized()

    def _on_max(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def _on_close(self):
        self.parent.close()

    # 【2026-10-06 四边缩放 + Aero Snap】—— 两条路都试过，结论如下：
    #   ① 手写 move()：窗口能跟手，但**完全没有**分屏（用户反馈"贴边无自适应"）。
    #   ② QWindow.startSystemMove()：走系统模态循环，但**对无边框窗口无效** ——
    #      Windows 认为这窗口没有 frame，不触发 Aero Snap（真机拖到边缘无反应）。
    #   → 最终方案：自己实现 Aero Snap —— 拖动时实时检测鼠标是否进入屏幕边缘
    #     热区，用 _SnapOverlay 画半透明预览框；松手时按 _snap_geometry 把窗口
    #     摆到半屏 / 四分之一屏 / 最大化。行为对齐 Win11 原生 Aero Snap。
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.parent._hit_test_enabled:
            # 记录"按下点相对窗口左上角"的偏移，拖动时保持鼠标位置不变
            self._drag_pos = ev.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
            # 【2026-10-06】开始拖动前先退出分屏态：若窗口还锁着分屏时放开的
            # 最小尺寸(0,0)，拖动/后续 showNormal 的尺寸行为会不可预期。
            # 注意 _snap_clear_min 内部保证"窗口比最小尺寸还小时推迟恢复"，
            # 不会在此刻把窗口突然撑大。
            try:
                self.parent._snap_clear_min()
            except AttributeError:
                pass
            self.parent._snap_begin()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None and ev.buttons() & Qt.LeftButton:
            w = self.parent
            # 最大化状态下拖动 → 先还原成普通窗口，行为与原生一致
            if w.isMaximized():
                # 还原后让窗口继续跟随鼠标：按比例还原尺寸
                w.showNormal()
                self._drag_pos = ev.globalPosition().toPoint() - w.frameGeometry().topLeft()
            w.move(ev.globalPosition().toPoint() - self._drag_pos)
            # 拖动中实时检测贴边 → 更新分屏预览
            w._snap_update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._drag_pos is not None:
            # 松手：若停在分屏热区就应用布局
            self.parent._snap_commit()
            self._drag_pos = None
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        """双击标题栏 = 最大化/还原。"""
        w = self.parent
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()
        ev.accept()


class QtBridge(QObject):
    """暴露给前端 JS 的原生桥（window.QtBridge）。

    当前 gui.html 仅在主题切换时调用 setTheme，且已做 `if (window.QtBridge)` 容错。
    这里用 runJavaScript 注入一个轻量桥对象，主题变更通过 _poll_theme 拉取，
    因此无需 QWebChannel 也能工作。
    """

    def __init__(self, parent_win):
        super().__init__()
        self.parent_win = parent_win

    @Slot(str)
    def setTheme(self, theme: str):
        try:
            self.parent_win.apply_native_theme(theme)
        except Exception:
            pass


class XianRenZhangWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("仙人掌 Agent")
        # 最小尺寸用模块常量（见 _min_win_size 的说明：不能拿
        # self.minimumSize() 当原值保存，会被布局的 hint 污染）
        self.setMinimumSize(_min_win_size())
        self.resize(1180, 800)
        # 【2026-10-06】四边四角缩放 + Aero Snap
        # 实现方式见 _install_resize_hotzones 的说明（用透明热区 +
        # startSystemResize；早期试过 WM_NCHITTEST 但无边框窗口收不到）。
        self._hit_test_enabled = True
        # 最近一次命中的 HT* 值（测试桥读取用）
        self._last_hit = HTCLIENT

        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        elif os.path.exists(ICON_PNG):
            self.setWindowIcon(QIcon(ICON_PNG))

        # 无边框：用自定义标题栏
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        self.view = QWebEngineView()
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        self.view.setPage(QWebEnginePage(self.view))
        root.addWidget(self.view, 1)

        # 【2026-10-06】缩放手柄：以前只有右下角一个 QSizeGrip（用户抱怨
        # "只有右下角能拖"）。现在四边 + 四角都装透明热区，按下走
        # startSystemResize（并白送 Aero Snap），QSizeGrip 已多余，
        # 且它固定在右下角会与角热区重叠，故移除。
        # 保留 self._grip = None 以兼容 resizeEvent 等处的空值判断。
        self._grip = None
        # 四边四角缩放热区（必须在 centralWidget 建好之后）
        self._hotzones = []
        self._install_resize_hotzones()

        # ── Aero Snap（2026-10-06）──────────────────────────────
        # 拖动标题栏时实时检测鼠标是否进入屏幕边缘热区，用独立顶层窗口
        # _SnapOverlay 画半透明预览，松手时按 _snap_geometry 应用布局。
        # 为什么不用 QWindow.startSystemMove：它对【无边框窗口】不触发
        # Aero Snap（Windows 认为这窗口没有 frame），实测贴边无任何反应。
        self._snap_overlay = _SnapOverlay(QApplication.instance())
        self._snap_layout = None       # 当前预览的布局 key（None=不在热区）
        self._snap_normal_geo = None   # 拖动前的窗口几何，松手若无布局则恢复
        self._snap_dragging = False
        self._last_snap = None         # 最近一次应用的布局（测试桥读取用）
        self._snap_active = False      # 当前是否处于分屏状态（最小尺寸已放开）
        self._snap_min_saved = None    # 分屏前的最小尺寸，取消分屏时恢复
        self._snap_min_pending = None  # 窗口还太小时推迟恢复的最小尺寸
        # 分屏【之前】的原始几何：用户把窗口从分屏拖出来时恢复成这个尺寸
        # （对齐 Win11 原生 Aero Snap 行为）
        self._snap_restore_geo = None

        # ── 测试专用桥（只在 XRZ_GUI_BRIDGE=1 时激活）──────────────
        # 作用：让自动化测试能从真实窗口进程里读写面板 DOM、打印 PDF、截图。
        # 不设置该环境变量时此代码完全不激活，不影响正常使用。
        if os.environ.get("XRZ_GUI_BRIDGE") == "1":
            self._init_test_bridge()

        # 主题轮询
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(800)
        self._theme_timer.timeout.connect(self._poll_theme)
        self._theme_timer.start()

        self.apply_native_theme("dark")
        self._inject_bridge()

        # 启动后端并加载页面
        self._backend_proc = None
        self._start_backend()
        self._check_ready()

    # ── 原生桥注入 ──
    def _inject_bridge(self):
        try:
            js = (
                "window.QtBridge = window.QtBridge || { setTheme: function(t){"
                "  try{ window.__xrzTheme = t; }catch(e){} } };"
            )
            self.view.page().runJavaScript(js)
        except Exception as e:
            log("注入 QtBridge 失败（可忽略）:", e)

    # ── 主题 ──
    def _poll_theme(self):
        try:
            self.view.page().runJavaScript(
                "document.documentElement.classList.contains('light')",
                self._cb,
            )
        except Exception:
            pass

    def _cb(self, val):
        try:
            self.apply_native_theme("light" if val else "dark")
        except Exception:
            pass

    def apply_native_theme(self, theme):
        css = self._light_css() if theme == "light" else self._css()
        self.setStyleSheet(css)

    def resizeEvent(self, ev):
        # 【2026-10-06】窗口尺寸变了，重排四边四角缩放热区
        self._layout_hotzones()
        # 用户把分屏窗口手动放大到正常尺寸后，补上之前推迟的最小尺寸恢复
        try:
            self._snap_flush_pending_min()
        except AttributeError:
            pass
        super().resizeEvent(ev)

    def eventFilter(self, obj, ev):
        return super().eventFilter(obj, ev)

    # ── 四边四角缩放 + Aero Snap（2026-10-06 改用 startSystemResize）────
    # 【为什么不用 WM_NCHITTEST】先试过接管 WM_NCHITTEST 返回 HTLEFT/HTCAPTION，
    # 但在 Qt 无边框窗口（FramelessWindowHint）上 Qt 并不把该消息交给
    # Python 的 nativeEvent —— 真机实测所有探测点一律返回 HTCLIENT(1)/0，
    # 四边四角拖不动、贴边也不分屏。用户反馈"四角不能缩、贴边无自适应"，
    # 就是这条路走不通的表现。
    # 【现在怎么做】改用 Qt 官方 startSystemResize(Qt.Edge)：
    #   ① 在窗口四周铺一圈「透明热区」控件（4条边 + 4个角），它们是真正的
    #      Qt 子控件，能正常收到鼠标事件；
    #   ② 鼠标按下时调 startSystemResize(对应边)，缩放交给系统的模态循环，
    #      手感与原生一致，且**自动获得 Aero Snap**（贴边/贴角 1/2、1/4 分屏、
    #      顶端最大化）—— 这些本来就是 Windows 对可缩放窗口的系统能力；
    #   ③ 标题栏用 startSystemMove()，同样获得贴边分屏与双击最大化。
    # 代价：热区会盖住内容最外侧 6px，故热区做成半透明"贴边提手"样式，
    #      鼠标移上去才显形，不影响观感。
    def _install_resize_hotzones(self):
        """在窗口四周铺 4 边 + 4 角透明热区，鼠标按下走 startSystemResize。"""
        m = _RESIZE_MARGIN
        host = self.centralWidget()      # 热区必须是 centralWidget 的子控件
        if host is None:
            return
        self._hz_margin = m
        self._hotzones = []
        for zone in ("top", "bottom", "left", "right", "tl", "tr", "bl", "br"):
            hz = _ResizeHotZone(self, zone)
            hz.setParent(host)
            hz.show()
            hz.raise_()
            self._hotzones.append(hz)
        self._layout_hotzones()

    def _layout_hotzones(self):
        """窗口尺寸变化时重排热区（边条贴四边、角块盖在角上）。"""
        hzs = getattr(self, "_hotzones", None)
        if not hzs:
            return
        host = self.centralWidget()
        if host is None:
            return
        w, h = host.width(), host.height()
        m = getattr(self, "_hz_margin", _RESIZE_MARGIN)
        c = m * 2                                   # 角块边长
        geo = {
            "top":    (0, 0, w, m),
            "bottom": (0, h - m, w, m),
            "left":   (0, 0, m, h),
            "right":  (w - m, 0, m, h),
            "tl": (0, 0, c, c),
            "tr": (w - c, 0, c, c),
            "bl": (0, h - c, c, c),
            "br": (w - c, h - c, c, c),
        }
        for hz in hzs:
            g = geo.get(hz.zone)
            if g and g[2] > 0 and g[3] > 0:
                hz.setGeometry(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                hz.raise_()

    # ── Aero Snap：拖动贴边分屏（2026-10-06）─────────────────────
    def _screen_area_at(self, gx, gy):
        """取鼠标所在屏幕的可用区域（排除任务栏），返回 (x, y, w, h)。"""
        try:
            scr = QApplication.screenAt(QPoint(int(gx), int(gy)))
        except (AttributeError, TypeError):
            scr = None
        if scr is None:
            scr = self.screen() or QApplication.primaryScreen()
        if scr is None:
            return None
        a = scr.availableGeometry()
        return a.x(), a.y(), a.width(), a.height()

    def _snap_begin(self):
        """开始拖动：记下当前几何（分屏取消时要恢复）。"""
        self._snap_dragging = True
        self._snap_layout = None
        self._snap_normal_geo = self.geometry()

    def _snap_update(self):
        """拖动中：检测鼠标是否进入屏幕边缘热区，据此更新/隐藏预览框。"""
        if not getattr(self, "_snap_dragging", False):
            return
        from PySide6.QtGui import QCursor
        gp = QCursor.pos()
        area = self._screen_area_at(gp.x(), gp.y())
        if not area:
            return
        layout = _compute_snap_layout(gp.x(), gp.y(), area)
        self._snap_layout = layout
        geo = _snap_geometry(layout, area) if layout else None
        if geo:
            self._snap_overlay.show_layout(geo)
        else:
            self._snap_overlay.hide_overlay()

    def _snap_commit(self):
        """松手：若停在分屏热区，应用布局；否则恢复拖动前几何。"""
        if not getattr(self, "_snap_dragging", False):
            return
        self._snap_dragging = False
        self._snap_overlay.hide_overlay()
        layout = self._snap_layout
        self._snap_layout = None
        if not layout:
            # 【2026-10-06 修"拖动后窗口自动跑回原位" + "拖出来不恢复原尺寸"】
            # ① 之前 setGeometry(self._snap_normal_geo) 会把窗口强行拉回
            #    【本次拖动开始前】的位置 —— 分屏过一次后，用户随便拖两下松手
            #    就被拽回原来的左下角/半屏，看起来像"被吸住、拖不动"。
            # ② 但完全不恢复尺寸也不行：Win11 原生行为是"从分屏拖出来 →
            #    恢复成分屏之前的原始尺寸"，而不是保持那个半屏/1/4 屏小窗。
            # 所以：位置用拖动后的（move 已处理好），尺寸只在【之前分过屏】
            # 时恢复成 _snap_restore_geo（分屏前的原始大小），并让窗口左上角
            # 跟着鼠标走，避免视觉上跳。
            self._snap_clear_min()
            _rg = getattr(self, "_snap_restore_geo", None)
            if _rg is not None and not self.isMaximized():
                _cur = self.geometry()
                try:
                    self.setMinimumSize(0, 0)
                    self.setGeometry(_cur.x(), _cur.y(), _rg.width(), _rg.height())
                except (AttributeError, RuntimeError):
                    pass
            return
        # 取鼠标所在屏幕（松手时窗口已在该屏附近）
        g = self.frameGeometry()
        area = self._screen_area_at(g.center().x(), g.top() + 2)
        if not area:
            self._snap_normal_geo = None
            return
        geo = _snap_geometry(layout, area)
        self._snap_normal_geo = None
        if not geo:
            return
        x, y, w, h = geo
        if layout == "max":
            # 最大化不需要放开最小尺寸，先把之前的分屏状态收干净
            self._snap_clear_min()
            self._snap_restore_geo = None   # 最大化后拖出 = 恢复普通窗口，不需记
            self.showMaximized()
        else:
            # 先还原成普通窗口再设几何，否则对最大化窗口设置无效
            if self.isMaximized():
                self.showNormal()
            # 【2026-10-06 新增】记下【分屏之前】的原始几何。
            # 用户从分屏状态把窗口拖出来时，要恢复成这个尺寸（Win11 原生行为），
            # 而不是保持半屏/1/4 屏的小窗。已经处于分屏中则不覆盖，
            # 这样连续贴两次边也仍能回到最初的原始尺寸。
            if not getattr(self, "_snap_active", False):
                try:
                    if self._snap_restore_geo is None:
                        self._snap_restore_geo = self.geometry()
                except (AttributeError, RuntimeError):
                    pass
            # 【2026-10-06 修"分屏尺寸被最小尺寸卡住"】
            # 1/4 屏在 1714 逻辑宽的屏上只有 857x547，小于本窗口的
            # setMinimumSize(900, 620)，setGeometry 会被夹住（实测 got=
            # (0,0,900,620)，位置对了尺寸被撑坏，四分屏彻底失效）。
            # 只放开窗口的最小尺寸不够 —— QMainWindow 的布局会拿
            # centralWidget / QWebEngineView 的 minimumSizeHint 顶回来，
            # 必须把这几层的最小尺寸一起临时清掉，设完再恢复。
            #
            # 【注意】这里必须用 Qt 的 setGeometry，**不能用 Win32 SetWindowPos**：
            # SetWindowPos 收的是物理像素，而 _snap_geometry 用的是 Qt 逻辑像素。
            # 本机 175% 缩放下逻辑宽 1714 = 物理 3000，混用会让尺寸全错
            # （实测 857 物理被 Qt 读成 490 逻辑）。setGeometry 由 Qt 负责
            # DPI 换算，才是正确选择。
            #
            # 【关键】最小尺寸必须【持续】放开，不能设完就恢复：
            # setGeometry 是异步生效的，Qt 会在下一轮事件循环里按当前
            # minimumSize 重算尺寸。若在 finally 里立刻把 900x620 装回去，
            # 857 会被顶回 900（实测 min_win=[900,620] 而 geom=[0,0,900,1095]，
            # 位置对、宽度被钉死）。所以：分屏期间把窗口最小尺寸降为 0 并
            # 记下原值，等用户取消分屏（_snap_clear_min）或最大化时再恢复。
            # 【2026-10-06 修"最小尺寸恢复失效"】
            # 不能用 self.minimumSize() 存原值 —— QMainWindow 布局的
            # minimumSizeHint 会把 minimumSize 覆盖成一个很小的值
            # （实测读回 [235, 38]，正是标题栏+侧栏的 hint），恢复时装回
            # 235x38 等于【根本没恢复】，窗口能被缩到 400x300。
            # 正确做法：把正常最小尺寸定义为模块常量 _MIN_WIN，
            # 由 __init__ 统一 setMinimumSize 设定，恢复时直接用它。
            if self._snap_min_saved is None:
                self._snap_min_saved = _min_win_size()
            try:
                self.setMinimumSize(0, 0)
                self.centralWidget().setMinimumSize(0, 0)
            except (AttributeError, RuntimeError):
                pass
            self.setGeometry(x, y, w, h)
            self._snap_active = True
        self._last_snap = layout

    def _snap_clear_min(self, keep_geometry=False):
        """取消分屏 / 最大化时，把窗口最小尺寸恢复到正常值。

        【2026-10-06 修"拖动时窗口被拉回/跳回原位"】
        之前这里直接 setMinimumSize(900, 620)，而分屏窗口当前尺寸可能只有
        857x547 —— Qt 一旦最小尺寸变大超过当前尺寸，会**立刻把窗口撑大并
        锚在左上角**，表现为：分屏后想拖走窗口，它却猛地跳回原处/左上角。
        现在：只有当窗口当前尺寸已经不小于原最小尺寸时才恢复；
        否则推迟 —— 记下 pending，等用户下次手动放大或最大化时再恢复。
        keep_geometry=True 时额外保证不改动窗口几何。
        """
        if not getattr(self, "_snap_active", False):
            return
        self._snap_active = False
        _m = getattr(self, "_snap_min_saved", None)
        self._snap_min_saved = None
        if _m is None:
            return
        try:
            cur = self.size()
            if cur.width() < _m.width() or cur.height() < _m.height():
                # 窗口比分屏尺寸还小 → 暂不恢复，等它被放大/最大化后再恢复
                self._snap_min_pending = _m
                return
            self.setMinimumSize(_m)
        except (AttributeError, RuntimeError):
            pass

    def _snap_flush_pending_min(self):
        """若之前推迟过最小尺寸恢复，在窗口已足够大时补上。"""
        _m = getattr(self, "_snap_min_pending", None)
        if _m is None:
            return
        try:
            cur = self.size()
            if cur.width() >= _m.width() and cur.height() >= _m.height():
                self.setMinimumSize(_m)
                self._snap_min_pending = None
        except (AttributeError, RuntimeError):
            pass

    # ── 后端管理 ──
    def _start_backend(self):
        if backend_healthy():
            log("后端已在运行，跳过启动")
            return
        env = os.environ.copy()
        env["XRZ_NO_GUI"] = "1"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_PATH)
        term = os.path.join(APP_DIR, TERMINAL_PY)
        if not os.path.exists(term):
            log("[错误] 找不到后端入口:", term)
            return
        # 【#86 可迁移 2026-09-24】冻结模式（PyInstaller 包）：优先用包内自带的
        # 后端可执行文件（XianRenZhangBackend.exe），整个文件夹拷到任意电脑/盘符
        # 都能直接跑，不再依赖 D:\软件\Python 或系统里装了 playwright 的解释器。
        if getattr(sys, "frozen", False):
            backend_exe = os.path.join(APP_DIR, "XianRenZhangBackend.exe")
            if os.path.exists(backend_exe):
                py = backend_exe
                # 包内解释器已含 playwright，直接起 exe；环境变量继承即可
                subprocess.Popen(
                    [py],
                    cwd=APP_DIR,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                )
                log("已启动后端（包内 exe）:", py)
                return
        py = _detect_pythonw() or sys.executable
        try:
            self._backend_proc = subprocess.Popen(
                [py, term],
                cwd=APP_DIR,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            )
            log("已启动后端:", py, term)
        except Exception as e:
            log("[错误] 启动后端失败:", e)

    def _check_ready(self):
        # 轮询后端就绪后加载 GUI
        self._ready_ok = False

        def _try():
            if backend_healthy():
                if not self._ready_ok:
                    self._ready_ok = True
                    self._load_gui()
                return
            # 还没好，继续等（最多 ~60s）
            if not hasattr(self, "_wait_n"):
                self._wait_n = 0
            self._wait_n += 1
            if self._wait_n > 60:
                # 超时也先加载，前端会显示离线
                self._load_gui()

        t = QTimer(self)
        t.setInterval(1000)
        t.timeout.connect(_try)
        t.start()
        # 立刻试一次
        _try()

    def _load_gui(self):
        gui = os.path.join(APP_DIR, "gui.html")
        if os.path.exists(gui):
            # 使用HTTP URL而不是本地文件，避免QWebEngine在沙箱环境下的问题
            self.view.setUrl(QUrl(BACKEND_URL + "/gui.html"))
            log("已加载 GUI:", BACKEND_URL + "/gui.html")
        else:
            log("[错误] 找不到 gui.html:", gui)

    def closeEvent(self, ev):
        # 【2026-09-26 修复"关 GUI 没关连带进程"】
        # 旧设计是「关窗只关面板，agent 后台继续跑」——于是关 GUI 后留下
        # 后端 8888 + 它拉起的 chromium / playwright driver / 子代理子进程，
        # 全成孤儿。现在默认关 GUI 就连带全清（见 shutdown_backend_and_children）：
        #   ① POST /shutdown 优雅关后端（关浏览器 + 停子代理 + flush 登录态）
        #   ② 兜底按 8888 端口 taskkill /T /F 杀残留整棵进程树
        # 保留旧语义可回退：XRZ_KEEP_BACKEND_ON_CLOSE=1 时关窗不动后端。
        # 用 QTimer.singleShot(0) 异步执行，让窗口先干净消失、不卡 UI。
        keep = os.environ.get("XRZ_KEEP_BACKEND_ON_CLOSE") == "1"
        if keep:
            log("窗口关闭（XRZ_KEEP_BACKEND_ON_CLOSE=1，后端保持运行）")
        else:
            log("窗口关闭 → 连带清理后端 + 浏览器 + 子代理")
            QTimer.singleShot(0, self._cleanup_on_close)
        ev.accept()

    def _cleanup_on_close(self):
        # Qt 事件循环里跑，closeEvent 已 accept 窗口已消失
        try:
            shutdown_backend_and_children(dry_run=False)
        except Exception as e:
            log("[WARN] 连带清理异常:", e)

    # ── 测试桥实现 ─────────────────────────────
    def _init_test_bridge(self):
        import json as _json
        import threading as _threading
        import queue as _queue
        import http.server as _hs
        import socketserver as _ss
        from PySide6.QtCore import QTimer as _QTimer

        _req_q = _queue.Queue()
        _pending_cbs = []   # 强引用保活 runJavaScript 的回调，防 GC

        class _Handler(_hs.BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    ln = int(self.headers.get("Content-Length", 0) or 0)
                    body = _json.loads(self.rfile.read(ln) or b"{}") if ln else {}
                except Exception:
                    body = {}
                ev = _threading.Event()
                box = {}
                _req_q.put((str(body.get("js") or "null"), body, ev, box))
                ev.wait(30)
                out = _json.dumps({"value": box.get("value")}, ensure_ascii=False).encode("utf-8")
                try:
                    _pending_cbs.clear()
                except Exception:
                    pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        try:
            srv = _ss.ThreadingTCPServer(("127.0.0.1", 9333), _Handler)
            srv.daemon_threads = True
            _threading.Thread(target=srv.serve_forever, daemon=True).start()
            log("[测试桥] 已监听 http://127.0.0.1:9333")
        except Exception as e:
            log("[测试桥] 启动失败:", e)
            return

        def _drain():
            while True:
                try:
                    js, body, ev, box = _req_q.get_nowait()
                except Exception:
                    break
                try:
                    kind = body.get("kind", "eval")
                    log("[测试桥] 收到请求 kind=", kind, " js=", (js or "")[:60])
                    if kind == "pdf":
                        def _pdf_done(ok, ev=ev, box=box):
                            box["value"] = bool(ok); ev.set()
                        path = body.get("path") or ""
                        try:
                            self.view.page().printToPdf(path)
                            # printToPdf 是异步信号，这里简单用定时器查文件
                            def _check(n=0, path=path, ev=ev, box=box):
                                import os as _os
                                if _os.path.exists(path) and _os.path.getsize(path) > 0:
                                    box["value"] = True; ev.set()
                                elif n > 60:
                                    box["value"] = False; ev.set()
                                else:
                                    _QTimer.singleShot(250, lambda: _check(n + 1, path, ev, box))
                            _check()
                        except Exception as e:
                            box["value"] = f"ERR {e}"; ev.set()
                    elif kind == "snap":
                        # 【2026-10-06】Aero Snap 真机验证入口。
                        # 不模拟鼠标（用户红线），故分两部分验证：
                        #   a) 纯函数 _compute_snap_layout / _snap_geometry
                        #      在真实屏幕区域上的判定与几何 —— 贴边判定对不对；
                        #   b) 真实调用 _snap_commit 应用布局，看窗口几何是否
                        #      真的变成半屏/1/4 屏 —— 端到端证明"贴边会分屏"。
                        _act = body.get("action") or "probe"
                        _tx = body.get("tx")
                        _ty = body.get("ty")
                        _tw = body.get("w")
                        _th = body.get("h")

                        def _snap(n=0, act=_act, tx=_tx, ty=_ty,
                                  tw=_tw, th=_th):
                            try:
                                _g = self.frameGeometry()
                                _area = self._screen_area_at(
                                    _g.center().x(), _g.top() + 2)
                                if act == "drag_to":
                                    # 模拟真实拖动序列（不碰鼠标）：
                                    # _snap_clear_min → _snap_begin → move →
                                    # _snap_update → _snap_commit
                                    self._snap_clear_min()
                                    self._snap_begin()
                                    if tx is not None and ty is not None:
                                        self.move(int(tx), int(ty))
                                        # 强制按目标点做一次热区判定
                                        self._snap_layout = _compute_snap_layout(
                                            int(tx), int(ty), _area) \
                                            if _area else None
                                    self._snap_commit()
                                    _q = self.geometry()
                                    box["value"] = _json.dumps({
                                        "geom": [_q.x(), _q.y(),
                                                 _q.width(), _q.height()],
                                        "snap_active": bool(self._snap_active),
                                        "min_win": [self.minimumSize().width(),
                                                    self.minimumSize().height()],
                                    }, ensure_ascii=False)
                                elif act == "resize_to":
                                    # 只调 resize，【绝不碰 minimumSize】——
                                    # 否则会把最小尺寸抹掉，测出来的"最小尺寸
                                    # 没恢复"是测试自己造成的假 FAIL。
                                    # 若最小尺寸真的生效，resize(400,300) 会被
                                    # Qt 夹回 >=900x620。
                                    self._snap_clear_min()
                                    if tw and th:
                                        self.resize(int(tw), int(th))
                                    _q = self.geometry()
                                    box["value"] = _json.dumps({
                                        "geom": [_q.x(), _q.y(),
                                                 _q.width(), _q.height()],
                                    }, ensure_ascii=False)
                                elif act == "min_info":
                                    box["value"] = _json.dumps({
                                        "min_win": [self.minimumSize().width(),
                                                    self.minimumSize().height()],
                                        "snap_active": bool(self._snap_active),
                                        "min_pending": list(
                                            self._snap_min_pending.toTuple())
                                        if getattr(self, "_snap_min_pending", None)
                                        is not None
                                        and hasattr(self._snap_min_pending,
                                                    "toTuple") else None,
                                    }, ensure_ascii=False)
                                elif act == "restore":
                                    self._snap_min_pending = None
                                    self._snap_min_saved = None
                                    self._snap_active = False
                                    self._snap_restore_geo = None
                                    try:
                                        self.setMinimumSize(_min_win_size())
                                    except (AttributeError, RuntimeError):
                                        pass
                                    if self.isMaximized():
                                        self.showNormal()
                                    self.setGeometry(200, 100, 1180, 800)
                                    _q = self.geometry()
                                    box["value"] = _json.dumps({
                                        "geom": [_q.x(), _q.y(),
                                                 _q.width(), _q.height()],
                                    }, ensure_ascii=False)
                                elif act == "probe":
                                    box["value"] = _json.dumps({
                                        "area": list(_area) if _area else None,
                                        "cur": self.geometry().getRect()
                                        if hasattr(self.geometry(), "getRect")
                                        else [self.geometry().x(),
                                              self.geometry().y(),
                                              self.geometry().width(),
                                              self.geometry().height()],
                                        "last_snap": self._last_snap,
                                        "has_overlay": self._snap_overlay is not None,
                                        "maximized": bool(self.isMaximized()),
                                    }, ensure_ascii=False)
                                else:
                                    # 真应用一次分屏（模拟"松手停在热区"）
                                    self._snap_layout = act
                                    self._snap_dragging = True
                                    self._snap_normal_geo = self.geometry()
                                    self._snap_commit()
                                    _q = self.geometry()
                                    box["value"] = _json.dumps({
                                        "applied": act,
                                        "geom": [_q.x(), _q.y(),
                                                 _q.width(), _q.height()],
                                        "expect": list(_snap_geometry(act, _area))
                                                 if _area and _snap_geometry(act, _area) else None,
                                        "maximized": bool(self.isMaximized()),
                                        # ---- 诊断：谁在卡最小尺寸 ----
                                        "min_win": list(self.minimumSize().toTuple())
                                        if hasattr(self.minimumSize(), "toTuple")
                                        else [self.minimumSize().width(),
                                              self.minimumSize().height()],
                                        "min_cw": list(
                                            self.centralWidget().minimumSize().toTuple())
                                        if self.centralWidget() is not None
                                        and hasattr(
                                            self.centralWidget().minimumSize(),
                                            "toTuple") else None,
                                        "min_view": list(
                                            self.view.minimumSize().toTuple())
                                        if hasattr(getattr(self, "view", None),
                                                   "minimumSize") else None,
                                        "layout_min": list(
                                            self.centralWidget().layout().minimumSize().toTuple())
                                        if (self.centralWidget() is not None
                                            and self.centralWidget().layout() is not None
                                            and hasattr(
                                                self.centralWidget().layout().minimumSize(),
                                                "toTuple")) else None,
                                        "sizehint_min": list(
                                            self.minimumSizeHint().toTuple())
                                        if hasattr(self.minimumSizeHint(), "toTuple")
                                        else [self.minimumSizeHint().width(),
                                              self.minimumSizeHint().height()],
                                    }, ensure_ascii=False)
                                ev.set()
                            except Exception as _e:
                                box["value"] = "ERR %r" % (_e,)
                                ev.set()

                        _QTimer.singleShot(0, _snap)
                    elif kind == "hotzone":
                        # 【2026-10-06】进程内读缩放热区真实布局，用于验证
                        # "四边+四角都能缩放"（不再依赖 WM_NCHITTEST，那条路
                        # 在无边框 Qt 窗口上收不到消息，已废弃）。
                        def _hz(n=0):
                            try:
                                _hzs = getattr(self, "_hotzones", []) or []
                                _out = []
                                for _z in _hzs:
                                    _g = _z.geometry()
                                    _edges, _cur = _z.ZONES.get(_z.zone, (None, None))
                                    # Qt.Edge 是 flag 枚举，不能直接 int()；
                                    # 手动累加各方向位值（Left=1 Top=2 Right=4 Bottom=8）
                                    _ev = 0
                                    if _edges is not None:
                                        _e = _edges
                                        for _bit, _flag in ((1, Qt.LeftEdge),
                                                           (2, Qt.TopEdge),
                                                           (4, Qt.RightEdge),
                                                           (8, Qt.BottomEdge)):
                                            try:
                                                if _e & _flag:
                                                    _ev |= _bit
                                            except TypeError:
                                                # 老版本 PySide6 不支持 & 运算
                                                if _e == _flag:
                                                    _ev |= _bit
                                    _cv = 0
                                    try:
                                        _cv = int(_cur.value) if _cur is not None else 0
                                    except Exception:
                                        _cv = 0
                                    _out.append({
                                        "zone": _z.zone,
                                        "x": _g.x(), "y": _g.y(),
                                        "w": _g.width(), "h": _g.height(),
                                        "edge_val": _ev,
                                        "cursor": _cv,
                                        "visible": bool(_z.isVisible()),
                                        # 该热区能否触发系统缩放（要有 windowHandle）
                                        "can_resize": bool(
                                            self.windowHandle() is not None
                                            and not (self.isMaximized() or self.isFullScreen())
                                        ),
                                    })
                                box["value"] = _json.dumps({
                                    "zones": _out,
                                    "win_w": self.width(), "win_h": self.height(),
                                    "cw": self.centralWidget().width() if self.centralWidget() else 0,
                                    "ch": self.centralWidget().height() if self.centralWidget() else 0,
                                    "maximized": bool(self.isMaximized()),
                                    "has_handle": self.windowHandle() is not None,
                                }, ensure_ascii=False)
                                ev.set()
                            except Exception as _e:
                                box["value"] = "ERR %r" % (_e,)
                                ev.set()

                        _QTimer.singleShot(0, _hz)
                    elif kind == "shot":
                        # 【2026-09-21 修复】旧实现用 self.view.grab() 直接截 QtWebEngine，
                        # 在这个（软件渲染/no-sandbox）环境里拿到的永远是**全黑图** ——
                        # grab() 读不到 WebEngine 的合成层。改为「先 printToPdf 再光栅化」：
                        # printToPdf 走的是页面自己的打印布局，能真实拿到渲染结果，
                        # 再用 PyMuPDF 把第 1 页转成 PNG。fitz 不可用时退回 grab()。
                        _target = body.get("path") or ""

                        def _shot(ev=ev, box=box, target=_target):
                            import os as _os
                            tmp = _os.path.join(
                                _os.path.dirname(target) or ".", "_bridge_shot_tmp.pdf")
                            try:
                                self.view.page().printToPdf(tmp)
                            except Exception as e:
                                try:
                                    box["value"] = bool(self.view.grab().save(target))
                                except Exception as e2:
                                    box["value"] = f"ERR {e2}"
                                ev.set()
                                return

                            def _raster(n=0, tmp=tmp, target=target, ev=ev, box=box):
                                import os as _os
                                if _os.path.exists(tmp) and _os.path.getsize(tmp) > 0:
                                    try:
                                        import fitz  # PyMuPDF
                                        doc = fitz.open(tmp)
                                        pix = doc.load_page(0).get_pixmap(
                                            matrix=fitz.Matrix(1.5, 1.5))
                                        pix.save(target)
                                        doc.close()
                                        box["value"] = True
                                    except Exception as e:
                                        try:
                                            box["value"] = bool(self.view.grab().save(target))
                                        except Exception:
                                            box["value"] = f"ERR {e}"
                                    try:
                                        _os.remove(tmp)
                                    except Exception:
                                        pass
                                    ev.set()
                                    return
                                if n > 80:
                                    try:
                                        box["value"] = bool(self.view.grab().save(target))
                                    except Exception:
                                        box["value"] = False
                                    ev.set()
                                    return
                                _QTimer.singleShot(250, lambda: _raster(n + 1))

                            _raster()

                        _QTimer.singleShot(0, _shot)
                    else:
                        def _cb(v, ev=ev, box=box):
                            box["value"] = v; ev.set()
                        # 【坑】回调对象必须被强引用保住：PySide6 只保存弱引用，
                        # 闭包一旦被 GC，回调就再也回不来（表现为结果恒为空字符串）。
                        _pending_cbs.append(_cb)
                        try:
                            self.view.page().runJavaScript(js, _cb)
                        except Exception as _e:
                            log("[测试桥] runJavaScript 抛异常:", _e)
                            box["value"] = f"ERR {_e}"; ev.set()
                except Exception as e:
                    box["value"] = f"ERR {e}"
                    ev.set()
                log("[测试桥] 结果:", repr(box.get("value"))[:120])

        self._bridge_timer = _QTimer(self)
        self._bridge_timer.setInterval(30)
        self._bridge_timer.timeout.connect(_drain)
        self._bridge_timer.start()

    # ── 原生样式 ──
    def _css(self):
        return """
        QMainWindow, QWidget { background: #0f1115; color: #e6e6e6; }
        #TitleBar { background: #161a21; border-bottom: 1px solid #232a35; }
        #TitleLabel { color: #e6e6e6; font: 13px 'Microsoft YaHei'; }
        #TitleBtn { background: transparent; color: #c7ccd6; border: none; font: 14px 'Segoe UI'; }
        #TitleBtn:hover { background: #2a3140; }
        #TitleBtnDanger { background: transparent; color: #c7ccd6; border: none; font: 14px 'Segoe UI'; }
        #TitleBtnDanger:hover { background: #c0392b; color: #fff; }
        """

    def _light_css(self):
        return """
        QMainWindow, QWidget { background: #f5f6f8; color: #1f2430; }
        #TitleBar { background: #ffffff; border-bottom: 1px solid #e2e5ea; }
        #TitleLabel { color: #1f2430; font: 13px 'Microsoft YaHei'; }
        #TitleBtn { background: transparent; color: #4a5160; border: none; font: 14px 'Segoe UI'; }
        #TitleBtn:hover { background: #eceef2; }
        #TitleBtnDanger { background: transparent; color: #4a5160; border: none; font: 14px 'Segoe UI'; }
        #TitleBtnDanger:hover { background: #c0392b; color: #fff; }
        """


def main():
    _set_appusermodel_id()
    app = QApplication(sys.argv)
    app.setApplicationName("仙人掌 Agent")
    # 加载仙人掌图标
    try:
        if os.path.exists(ICON_PATH):
            icon = QIcon(ICON_PATH)
            if not icon.isNull():
                app.setWindowIcon(icon)
                log("已加载图标:", ICON_PATH)
        elif os.path.exists(ICON_PNG):
            icon = QIcon(ICON_PNG)
            if not icon.isNull():
                app.setWindowIcon(icon)
                log("已加载图标:", ICON_PNG)
    except Exception as e:
        log("[WARN] 图标加载失败:", e)
    win = XianRenZhangWindow()
    win.show()
    # 【真机修复】窗口已经拿到原生 HWND，把图标补到「窗口类」上，
    # 否则任务栏 / Alt+Tab 显示的是系统默认的空白窗口图标。
    _apply_win_class_icon(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
