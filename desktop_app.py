# -*- coding: utf-8 -*-
"""
仙人掌 Agent —— 原生桌面应用壳（PySide6）。

设计目标（来自用户反复强调的诉求）：
  - 像"独立软件"，不要浏览器外壳（无地址栏、无工具栏）。
  - 不要黑色控制台窗口（用 pythonw 启动）。
  - 绝不劫持/占用用户默认浏览器（后端 + 平台浏览器都是独立 Chromium 进程）。
  - 关闭窗口要干净地退出整个 agent（后端 + 它的 Chromium 子进程一起杀掉），
    而不是留下僵尸。

工作流：
  启动仙人掌.bat 用 pythonw 拉起本文件 → 本文件 subprocess 拉起 terminal.py
  （带 XRZ_NO_GUI=1，使其只做 HTTP 服务、不弹 Playwright 窗口）→ 轮询
  /health 就绪 → QWebEngineView 加载 http://127.0.0.1:8888 → 用户看到的就是
  一个原生窗口里的聊天界面。关窗时 taskkill /T /F 杀掉整棵进程树。
"""

import os
import sys
import time
import subprocess
import urllib.request

from PySide6.QtCore import Qt, QSize, QUrl, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QScreen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSizeGrip, QSpacerItem, QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

# ---- 路径 ----
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TERMINAL_PY = os.path.join(APP_DIR, "terminal.py")
ICON_PNG = os.path.join(APP_DIR, "__xianrenzhang_icon.png")
ICON_ICO = os.path.join(APP_DIR, "__xianrenzhang_icon.ico")
# 任务栏/alt-tab 优先用 .ico（多分辨率，缩放清晰）；没有再用 .png
ICON_PATH = ICON_ICO if os.path.exists(ICON_ICO) else ICON_PNG
PORT = 8888
BACKEND_URL = f"http://127.0.0.1:{PORT}"

# 把所有数据/浏览器二进制钉在 D 盘项目目录内，绝不写 C 盘。
# 复用 agent_core.xrz_paths 的同一套默认路径，保证前后端一致。
sys.path.insert(0, APP_DIR)
from agent_core.xrz_paths import PLAYWRIGHT_BROWSERS_PATH, DATA_ROOT  # noqa: E402


def log(*a):
    # pythonw 下没有控制台，print 会丢；用 flush 写到 stderr 也看不到。
    # 直接静默即可（原生窗口自身就是 UI）。
    pass


def _set_appusermodel_id():
    """修正任务栏图标：Windows 默认按 exe 分组取 pythonw.exe 的 Python 图标，
    导致 setWindowIcon 盖不住。设置一个显式 AppUserModelID 后，任务栏改用窗口图标。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            wintypes.LPCWSTR("XianRenZhang.Agent.Desktop.1")
        )
    except Exception:
        pass


def _detect_pythonw():
    """在所有可用 Python 里挑一个同时装了 playwright + PySide6 的来跑后端。

    优先级：
      1) 环境变量 XRZ_PYTHON 显式指定的解释器（支持 python.exe / pythonw.exe）
      2) PATH 里的 python / python3
      3) 常见安装目录（D:\\软件\\Python 等）
    命中后优先返回同目录下的 pythonw.exe（无黑窗），找不到再用 python.exe。
    """
    import shutil
    candidates = []
    if os.environ.get("XRZ_PYTHON"):
        candidates.append(os.environ["XRZ_PYTHON"])
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    for base in (r"D:\软件\Python", r"C:\Python312", r"C:\Python311",
                 r"C:\Users\X.LAPTOP-CA1GJQE3\.workbuddy\binaries\python\versions\3.13.12"):
        candidates.append(os.path.join(base, "python.exe"))

    seen = set()
    for cand in candidates:
        cand = os.path.abspath(cand)
        if cand in seen:
            continue
        seen.add(cand)
        if not os.path.exists(cand):
            continue
        # 该解释器是否同时具备 playwright + PySide6
        try:
            out = subprocess.run(
                [cand, "-c", "import playwright, PySide6"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            continue
        if out.returncode != 0:
            continue
        # 优先用同目录 pythonw（无黑窗）
        d = os.path.dirname(cand)
        for wname in ("pythonw.exe", "python.exe"):
            w = os.path.join(d, wname)
            if os.path.exists(w):
                return w
    return None



def backend_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


class TitleBar(QWidget):
    """自定义无边框标题栏：仙人掌图标 + 标题 + 最小/最大/关闭按钮，可拖拽。"""

    def __init__(self, parent: "XianRenZhangWindow"):
        super().__init__(parent)
        self.parent_win = parent
        self.setFixedHeight(38)
        self.setObjectName("titlebar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(8)

        # 仙人掌图标
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(22, 22)
        if os.path.exists(ICON_PNG):
            pix = QPixmap(ICON_PNG).scaled(
                22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.icon_label.setPixmap(pix)
        else:
            self.icon_label.setText("🌵")
        layout.addWidget(self.icon_label)

        # 标题
        self.title_label = QLabel("仙人掌 Agent")
        self.title_label.setObjectName("titlelabel")
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        self.title_label.setFont(font)
        layout.addWidget(self.title_label)

        layout.addItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # 窗口控制按钮
        self.btn_min = self._make_btn("—", self._on_min)
        self.btn_max = self._make_btn("▢", self._on_max)
        self.btn_close = self._make_btn("✕", self._on_close, danger=True)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)

        self._drag_pos = None

    def _make_btn(self, text, slot, danger=False):
        btn = QPushButton(text)
        btn.setFixedSize(34, 26)
        btn.setObjectName("titlebtn_danger" if danger else "titlebtn")
        btn.clicked.connect(slot)
        return btn

    def _on_min(self):
        self.parent_win.showMinimized()

    def _on_max(self):
        if self.parent_win.isMaximized():
            self.parent_win.showNormal()
        else:
            self.parent_win.showMaximized()

    def _on_close(self):
        self.parent_win.close()

    # ---- 拖拽 ----
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint()

    def mouseMoveEvent(self, ev):
        if self._drag_pos is not None:
            delta = ev.globalPosition().toPoint() - self._drag_pos
            self.parent_win.move(self.parent_win.pos() + delta)
            self._drag_pos = ev.globalPosition().toPoint()

    def mouseReleaseEvent(self, ev):
        self._drag_pos = None


class XianRenZhangWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.backend_proc = None
        self._closing = False

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setMinimumSize(900, 620)
        self.resize(1180, 760)

        # 居中
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(
                screen.center().x() - self.width() // 2,
                screen.center().y() - self.height() // 2,
            )
        except Exception:
            pass

        # 自定义样式
        self.setStyleSheet(self._css())

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        # 浏览器视图
        self.web = QWebEngineView()
        self.web.setObjectName("webframe")
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        root.addWidget(self.web, stretch=1)

        # 启动后端
        self._start_backend()

        # 轮询就绪
        self.loading_label = QLabel("正在启动仙人掌 Agent …")
        self.loading_label.setObjectName("loading")
        self.loading_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.loading_label, stretch=1)
        self.web.hide()
        self.loading_label.show()

        self._poll = QTimer(self)
        self._poll.setInterval(800)
        self._poll.timeout.connect(self._check_ready)
        self._poll.start()

    # ---------- 后端管理 ----------
    def _start_backend(self):
        # 若已存在健康后端，直接复用，不重复拉起
        if backend_healthy():
            return
        if not os.path.exists(TERMINAL_PY):
            return
        env = dict(os.environ)
        env["XRZ_NO_GUI"] = "1"
        # 无头开关透传：测试时设 XRZ_HEADLESS=1 让平台浏览器不弹可见窗口
        if os.environ.get("XRZ_HEADLESS"):
            env["XRZ_HEADLESS"] = os.environ["XRZ_HEADLESS"]
        # 显式钉死 D 盘数据目录与 Playwright 浏览器目录（与 xrz_paths 默认一致）
        env["XRZ_DATA_DIR"] = str(DATA_ROOT)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_PATH)
        try:
            pythonw = _detect_pythonw()
            if pythonw is None:
                log("未找到装有 playwright+PySide6 的 Python，请先运行「安装依赖.bat」")
                self.backend_proc = None
                return
            self.backend_proc = subprocess.Popen(
                [pythonw, TERMINAL_PY],
                cwd=APP_DIR,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception:
            self.backend_proc = None

    def _check_ready(self):
        if self._closing:
            return
        if backend_healthy():
            self._poll.stop()
            self.loading_label.hide()
            self.web.load(QUrl(BACKEND_URL))
            self.web.show()

    # ---------- 关闭：清掉整棵树 ----------
    def closeEvent(self, ev):
        if self._closing:
            ev.accept()
            return
        self._closing = True
        self._poll.stop()
        proc = self.backend_proc
        if proc is not None and proc.poll() is None:
            pid = proc.pid
            try:
                # /T 杀掉整棵进程树（含 terminal.py 拉起的 Chromium）
                # /F 强制。这样关窗 = 退出整个 agent，不留僵尸。
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        # 给一点时间让子进程退出
        QTimer.singleShot(300, QApplication.quit)
        ev.accept()

    # ---------- 样式 ----------
    def _css(self):
        return """
        QMainWindow, QWidget { background: #0f1115; }
        #titlebar {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #1b2a1b, stop:1 #0f1115);
            border-bottom: 1px solid #1f3a24;
        }
        #titlelabel { color: #c8e6c9; padding-left: 4px; }
        #titlebtn {
            background: transparent; color: #b0b8c0; border: none;
            font-size: 13px; border-radius: 4px;
        }
        #titlebtn:hover { background: #2a2f3a; color: #ffffff; }
        #titlebtn_danger { background: transparent; color: #ff8a80; border: none;
            font-size: 13px; border-radius: 4px; }
        #titlebtn_danger:hover { background: #c0392b; color: #ffffff; }
        #webframe { border: none; background: #0f1115; }
        #loading { color: #8bc34a; font-size: 14px; background: #0f1115; }
        """


def main():
    # AppUserModelID 必须在任何窗口/QApplication 之前设置，
    # Windows 才能把任务栏图标归属到这个 AppID 而不是 pythonw.exe。
    # 同时设置应用级窗口图标（有些 Windows 版本用 app 图标渲染任务栏）。
    _set_appusermodel_id()
    app = QApplication(sys.argv)
    app.setApplicationName("仙人掌 Agent")
    app.setApplicationDisplayName("仙人掌 Agent")
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = XianRenZhangWindow()
    # 窗口级图标（覆盖标题栏左上角）
    if os.path.exists(ICON_PATH):
        win.setWindowIcon(QIcon(ICON_PATH))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
