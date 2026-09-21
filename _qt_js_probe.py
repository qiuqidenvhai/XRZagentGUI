# -*- coding: utf-8 -*-
"""最小验证：PySide6 WebEngine 的 runJavaScript 回调在这个环境里到底能不能用"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QTimer, QUrl

app = QApplication(sys.argv)
v = QWebEngineView()
v.setHtml("<html><body><div id=x>hi</div></body></html>")
v.resize(400, 300)
v.show()

state = {"loaded": False, "cb1": None, "cb2": None}


def on_load(ok):
    state["loaded"] = ok
    print("loadFinished:", ok, flush=True)


v.page().loadFinished.connect(on_load)


def step1():
    print("step1: runJavaScript('1+1', cb)", flush=True)
    try:
        v.page().runJavaScript("1+1", lambda r: (state.__setitem__("cb1", r), print("  cb1 ->", r, flush=True)))
    except Exception as e:
        print("  step1 EXC:", e, flush=True)


def step2():
    print("step2: runJavaScript('document.getElementById(\"x\").innerText', cb)", flush=True)
    v.page().runJavaScript('document.getElementById("x").innerText',
                           lambda r: (state.__setitem__("cb2", r), print("  cb2 ->", repr(r), flush=True)))


def done():
    print("STATE:", state, flush=True)
    app.quit()


QTimer.singleShot(1500, step1)
QTimer.singleShot(4000, step2)
QTimer.singleShot(8000, done)
sys.exit(app.exec())
