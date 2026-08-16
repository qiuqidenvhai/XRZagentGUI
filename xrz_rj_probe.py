import sys, traceback
print("start", flush=True)
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtCore import QUrl, QEventLoop
    print("imports ok", flush=True)

    app = QApplication(sys.argv)
    print("QApplication ok", flush=True)
    view = QWebEngineView()
    loop = QEventLoop()
    result = {}

    def on_load(ok):
        print("loadFinished ok=", ok, "url=", view.url().toString(), flush=True)
        try:
            view.page().runJavaScript(
                "(()=>{ const d=document.createElement('div'); d.id='__probe';"
                " d.textContent='HELLO_JS'; document.body.appendChild(d);"
                " return 'TITLE='+document.title+'|PROBE='+(document.querySelector('#__probe')?document.querySelector('#__probe').textContent:'NONE'); })()",
                lambda v: (_store(v), loop.quit()))
        except Exception as e:
            print("runJavaScript call err:", e, flush=True)
            loop.quit()

    def _store(v):
        result["val"] = v
        print("JS returned:", repr(v), flush=True)

    view.loadFinished.connect(on_load)
    view.load(QUrl("http://127.0.0.1:8888"))
    print("loading...", flush=True)
    loop.exec()
    print("RJ result:", result.get("val"), flush=True)
    print("RJ_OK" if result.get("val") else "RJ_FAIL", flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(2)
