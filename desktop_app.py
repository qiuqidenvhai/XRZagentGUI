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
ICON_PATH = ICON_ICO if os.path.exists(ICON_ICO) else ICON_PNG
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


def _detect_pythonw():
    """找到带 playwright + PySide6 的 pythonw.exe（无黑窗）。"""
    candidates = []
    # 1) 同目录的 pythonw
    here = dirname(abspath(__file__))
    candidates.append(os.path.join(here, "pythonw.exe"))
    # 2) 常见安装路径
    candidates.append(r"D:\软件\Python\pythonw.exe")
    candidates.append(r"D:\软件\Python\python.exe")
    # 3) 当前解释器
    candidates.append(sys.executable)
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

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None and ev.buttons() & Qt.LeftButton:
            self.parent.move(ev.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None
        super().mouseReleaseEvent(ev)


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
        self.setMinimumSize(900, 620)
        self.resize(1180, 800)

        if os.path.exists(ICON_ICO):
            self.setWindowIcon(QIcon(ICON_ICO))
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

        # 右下角缩放手柄
        self._grip = QSizeGrip(self)

        # ── 测试专用桥（只在 XRZ_GUI_BRIDGE=1 时激活）──────────────
        # 作用：让自动化测试能从真实窗口进程里读写面板 DOM、打印 PDF、截图。
        # 不设置该环境变量时此代码完全不激活，不影响正常使用。
        if os.environ.get("XRZ_GUI_BRIDGE") == "1":
            self._init_test_bridge()
        self._grip.setFixedSize(18, 18)

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
        # 把缩放手柄放到右下角
        try:
            r = self.rect()
            self._grip.move(r.right() - 18, r.bottom() - 18)
        except Exception:
            pass
        super().resizeEvent(ev)

    def eventFilter(self, obj, ev):
        return super().eventFilter(obj, ev)

    # ── 后端管理 ──
    def _start_backend(self):
        if backend_healthy():
            log("后端已在运行，跳过启动")
            return
        py = _detect_pythonw() or sys.executable
        env = os.environ.copy()
        env["XRZ_NO_GUI"] = "1"
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_PATH)
        term = os.path.join(APP_DIR, TERMINAL_PY)
        if not os.path.exists(term):
            log("[错误] 找不到后端入口:", term)
            return
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
        # 关闭窗口只关面板；后端（agent）继续在后台运行。
        # 若后端是我们拉起的且仍在跑，保持它运行。
        log("窗口关闭，agent 继续在后台运行")
        ev.accept()

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
                    elif kind == "shot":
                        def _shot(ev=ev, box=box):
                            try:
                                ok = self.view.grab().save(body.get("path") or "")
                                box["value"] = bool(ok)
                            except Exception as e:
                                box["value"] = f"ERR {e}"
                            ev.set()
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
        if os.path.exists(ICON_ICO):
            icon = QIcon(ICON_ICO)
            if not icon.isNull():
                app.setWindowIcon(icon)
                log("已加载图标:", ICON_ICO)
        elif os.path.exists(ICON_PNG):
            icon = QIcon(ICON_PNG)
            if not icon.isNull():
                app.setWindowIcon(icon)
                log("已加载图标:", ICON_PNG)
    except Exception as e:
        log("[WARN] 图标加载失败:", e)
    win = XianRenZhangWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
