# -*- coding: utf-8 -*-
"""
_auto_update_patch.py —— 仙人掌 Agent 自动更新端点热补丁（#87 2026-09-24）。

terminal.pyc 是编译产物，加不了新路由；这里在 GUIHandler 出现后热替换
do_POST / do_GET，补上：
  POST /check_update  {"install": false}  → 只查远端版本，回 {"has_update": bool, ...}
                      {"install": true}   → 查 + 下载 + 覆盖安装（绝不动 xrz_data）
                                              回 {"ok": true, "need_restart": true, "message": ...}
  GET  /version       → 回本地 _version.json（{version, stamp}），前端展示用。

安全性：
  - 只改 GUIHandler 的两个方法，其它路由全部委托原实现；
  - auto_update.apply_update 内部白名单过滤，绝不碰 xrz_data / 日志 / 浏览器目录；
  - 任何异常都回 500 JSON，不崩后端。
"""

import json
import os
import threading
import time


def _do_patch_once(main_module, app_dir):
    handler = main_module.__dict__.get("GUIHandler")
    if handler is None or not hasattr(handler, "do_POST"):
        return False
    if getattr(handler.do_POST, "_xrz_autoupd_patched", False):
        return True

    try:
        import agent_core.auto_update as _au
        _root = _au._install_root()

        def _send_json_safely(self, code, obj):
            try:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                pass

        orig_post = handler.do_POST

        def _new_do_POST(self):
            try:
                p = getattr(self, "path", "") or ""
            except Exception:
                p = ""
            if p.startswith("/check_update"):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    payload = {}
                try:
                    if payload.get("install"):
                        r = _au.perform_update(_root)
                        code = 200 if r.get("ok") else 500
                        _send_json_safely(self, code, r)
                    else:
                        chk = _au.check_update(_root)
                        _send_json_safely(self, 200, chk)
                except Exception as e:
                    _send_json_safely(self, 500, {"ok": False, "message": str(e)})
                return
            # POST /restart —— 更新完成后立即重启（新进程加载新文件）
            if p.startswith("/restart"):
                try:
                    _au.restart_self(_root)
                except Exception as e:
                    _send_json_safely(self, 500, {"ok": False, "message": str(e)})
                    return
                # 先回包再让进程走（新进程 detached 已起，旧进程自杀）
                _send_json_safely(self, 200, {"ok": True, "message": "重启中"})
                try:
                    import os as _os
                    _os._exit(0)
                except Exception:
                    pass
                return
            orig_post(self)

        _new_do_POST._xrz_autoupd_patched = True
        _new_do_POST.__name__ = "do_POST"
        handler.do_POST = _new_do_POST

        # GET /version
        orig_get = getattr(handler, "do_GET", None)
        if orig_get is not None and not getattr(orig_get, "_xrz_autoupd_patched_get", False):
            def _new_do_GET(self):
                try:
                    p = getattr(self, "path", "") or ""
                except Exception:
                    p = ""
                if p.startswith("/version"):
                    try:
                        v, ver = _au._read_local_stamp(_root)
                        _send_json_safely(self, 200, {"stamp": v, "version": ver})
                    except Exception as e:
                        _send_json_safely(self, 500, {"error": str(e)})
                    return
                orig_get(self)

            _new_do_GET._xrz_autoupd_patched_get = True
            _new_do_GET.__name__ = "do_GET"
            handler.do_GET = _new_do_GET

        return True
    except Exception:
        return False


def install(main_module, app_dir, timeout_s=120):
    def _worker():
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if _do_patch_once(main_module, app_dir):
                    marker = os.path.join(app_dir, "_auto_update_patch_installed.txt")
                    try:
                        with open(marker, "w", encoding="utf-8") as f:
                            f.write("installed_at=%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        pass
                    print("[XRZ-AutoUpdate] 自动更新端点已挂载（/check_update /version）", flush=True)
                    return
            except Exception as e:
                print("[XRZ-AutoUpdate] 安装线程异常:", e, flush=True)
            time.sleep(0.3)
        print("[XRZ-AutoUpdate] 安装超时，补丁未生效", flush=True)

    t = threading.Thread(target=_worker, daemon=True, name="xrz-auto-update-patch")
    t.start()
    return t
