# -*- coding: utf-8 -*-
"""
profile_lock.py —— 同平台（同一份 user_data_dir）绝不双开的进程内互斥注册表。

背景（用户明确诉求）：
    "不要每次启动两个 deepseek 浏览器，有一个会掉登。"
    "同一个网页的不行（比如两个 deepseek），但一个 deepseek 一个豆包可以开两个浏览器。"

根因：
    主代理 browser.py:BrowserManager 与平台 platform_browser.py:
    PlatformBrowserManager("deepseek") 指向**同一个**目录
    browser_profiles/deepseek。谁后 launch 就会：
      1) 调 _kill_stale_browser_for_profile() 把先开的那个**活**浏览器杀掉，
      2) 再自己 launch_persistent_context 重开一个 → 表现为「两个 deepseek」
         且后开的把先开的登录态搞丢（掉登）。

修复语义（本模块落地）：
    - 以 user_data_dir（规范化绝对路径，小写）为键登记「活 owner」。
    - 后到者（无论来自 browser.py 还是 platform_browser.py）先 acquire()：
        * 若本目录已有**活** owner → 返回该 owner，调用方接管复用它的
          context/playwright（绝不重开、绝不 kill owner）；
        * 否则登记自己为 owner → 返回 None，调用方正常启动并允许清理
          跨进程孤儿残留（此时进程内确无活 owner，kill 是安全的）。
    - close() 时 owner release()，adopter（_adopted）只关自己的 page、不摘 registry。
    - **不同目录（跨平台）键不同，互不影响 → deepseek 与 豆包 天然并存多浏览器。**
"""
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_lock = threading.Lock()
# 规范化目录 key -> {"owner": manager_ref, "live": bool}
_owners: "Dict[str, dict]" = {}


def _key(user_data_dir: Any) -> str:
    try:
        return str(Path(user_data_dir).resolve()).lower()
    except Exception:
        return str(user_data_dir).lower()


def owner_alive_check(owner: Any) -> bool:
    """跨类通用：判断某 manager 持有的 context 是否仍活着。

    browser.py:BrowserManager 与 platform_browser.py:PlatformBrowserManager
    都把持久化 context 存在 self._browser，故统一用 _browser 判断。
    _adopted/_is_child 的 manager 不是目录真正 owner，一律不算活 owner。
    """
    if owner is None:
        return False
    if getattr(owner, "_adopted", False) or getattr(owner, "_is_child", False):
        return False
    b = getattr(owner, "_browser", None)
    if b is None:
        return False
    try:
        if hasattr(b, "is_closed") and b.is_closed():
            return False
    except Exception:
        # is_closed 抛异常通常意味着进程已死
        return False
    return True


def acquire(user_data_dir: Any, self_ref: Any,
            alive_check: Optional[Callable[[Any], bool]] = None) -> Optional[Any]:
    """尝试为 self_ref 抢占本目录的「活 owner」。

    返回值：
      - 非 None：同目录已有活 owner → 返回它（调用方接管复用，_adopted=True）。
      - None   ：self_ref 成为本目录 owner（调用方正常启动）。

    线程安全：用全局 threading.Lock 保护 registry 读写（launch 是协程，
    但 close/launch 可能交错，且 Windows kill 残留是同步 subprocess）。
    """
    alive_check = alive_check or owner_alive_check
    k = _key(user_data_dir)
    with _lock:
        ent = _owners.get(k)
        if ent and ent.get("live") and alive_check(ent["owner"]):
            # 已有活 owner → 接管
            return ent["owner"]
        # 无活 owner（或旧 owner 已死）→ 登记自己
        _owners[k] = {"owner": self_ref, "live": True}
    return None


def release(user_data_dir: Any, self_ref: Any) -> None:
    """owner 关闭 context 后摘除登记。仅当 self_ref 仍是当前 owner 才摘（幂等）。"""
    k = _key(user_data_dir)
    with _lock:
        ent = _owners.get(k)
        if ent and ent.get("owner") is self_ref:
            del _owners[k]


def is_owner(user_data_dir: Any, self_ref: Any) -> bool:
    """self_ref 是否仍是本目录登记的 owner。"""
    k = _key(user_data_dir)
    with _lock:
        ent = _owners.get(k)
        return bool(ent and ent.get("owner") is self_ref and ent.get("live"))


def count_live_owners() -> int:
    """诊断用：当前登记的活 owner 数量。"""
    with _lock:
        return sum(1 for e in _owners.values() if e.get("live") and owner_alive_check(e["owner"]))


def owner_summary() -> Dict[str, str]:
    """诊断用：{目录key: owner 类名}，供日志/排查。"""
    out = {}
    with _lock:
        for k, e in _owners.items():
            if e.get("live"):
                out[k] = type(e.get("owner")).__name__
    return out
