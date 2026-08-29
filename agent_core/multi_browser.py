"""
multi_browser.py — 多浏览器管理器统一入口

整合原有 multi_browser 和 platform_browser，保证向后兼容。
"""
import logging

logger = logging.getLogger("multi_browser")

# 导入 platform_browser 的统一接口
from agent_core.platform_browser import (
    PlatformBrowserManager,
    MultiPlatformManager,
    PLATFORM_PROFILES,
)

# 暴露给外部模块的全局单例
_multi_manager: MultiPlatformManager = None


def get_multi_browser_manager():
    """获取全局 MultiPlatformManager 单例"""
    global _multi_manager
    return _multi_manager


def set_multi_browser_manager(mgr: MultiPlatformManager):
    """设置全局 MultiPlatformManager"""
    global _multi_manager
    _multi_manager = mgr
