# -*- coding: utf-8 -*-
"""调试启动器：在跑 terminal.py 之前给 str.format / str.format_map 打监控补丁。

目的：定位后端启动时那个
    ValueError: Invalid format specifier '"done",...' for object of type 'str'
到底是谁在格式化哪段模板 —— 报错发生在 terminal.pyc 里，只有拿到模板原文 +
完整堆栈才能对症下药。

用法（必须先停掉已占 8888 的后端）：
    XRZ_NO_GUI=1 python _dbg_format.py
"""
import os
import sys
import runpy
import traceback

_orig_format = str.format
_orig_format_map = str.format_map
_SEEN = []


def _dump(kind, self, e):
    tmpl = repr(self)
    _SEEN.append((kind, tmpl))
    print("=" * 72, flush=True)
    print("[%s] 格式化失败: %s" % (kind, e), flush=True)
    print("模板原文 (前 1200 字符):", flush=True)
    print(tmpl[:1200], flush=True)
    print("-" * 72, flush=True)
    print("完整堆栈:", flush=True)
    traceback.print_exc()
    print("=" * 72, flush=True)


def _patched_format(self, *a, **kw):
    try:
        return _orig_format(self, *a, **kw)
    except Exception as e:                       # noqa: BLE001
        _dump("str.format", self, e)
        for i, k in enumerate(kw):
            print("  kw[%d] %s = %s" % (i, k, repr(kw[k])[:120]), flush=True)
        for i, v in enumerate(a):
            print("  arg[%d] = %s" % (i, repr(v)[:120]), flush=True)
        raise


def _patched_format_map(self, mapping):
    try:
        return _orig_format_map(self, mapping)
    except Exception as e:                       # noqa: BLE001
        _dump("str.format_map", self, e)
        raise


str.format = _patched_format
str.format_map = _patched_format_map

print("[dbg] str.format 监控补丁已装；即将运行 terminal.py", flush=True)
try:
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "terminal.py"),
                   run_name="__main__")
except SystemExit as e:
    print("[dbg] terminal.py SystemExit:", e, flush=True)
except BaseException:                            # noqa: BLE001
    print("[dbg] terminal.py 抛出未捕获异常:", flush=True)
    traceback.print_exc()
