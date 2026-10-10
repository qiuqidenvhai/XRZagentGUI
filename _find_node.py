# -*- coding: utf-8 -*-
"""查找可用的 node.exe（开发期自测工具用）。

【为什么】原来 4 个自测脚本各自写死了一个具体版本的 node 绝对路径，
换机器 / node 升级后全部失效。这里改成动态查找：
  1. NODE 环境变量
  2. PATH 上的 node
  3. 常见安装位置（Program Files / nvm / 用户目录）
找不到时返回 None，由调用方给出明确报错，而不是拿一个不存在的路径去跑。
"""
from __future__ import annotations

import glob
import os
import shutil
import sys


def find_node() -> str | None:
    env = os.environ.get("NODE", "").strip()
    if env and os.path.isfile(env):
        return env

    p = shutil.which("node")
    if p:
        return p

    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    pats = [
        os.path.join(home, ".workbuddy", "binaries", "node", "versions", "*", "node.exe"),
        os.path.join(home, ".workbuddy", "binaries", "node", "versions", "*", "*", "node.exe"),
        os.path.join(home, "AppData", "Roaming", "nvm", "v*", "node.exe"),
        os.path.join(home, "AppData", "Local", "Programs", "*", "node.exe"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
    ]
    for pat in pats:
        hits = [h for h in glob.glob(pat) if os.path.isfile(h)]
        if hits:
            # 优先版本号更大的（22.22.3 > 22.22.2）
            def _key(h):
                import re
                nums = re.findall(r"(\d+)", os.path.basename(os.path.dirname(h)))
                return tuple(int(x) for x in nums) or (0,)
            return sorted(hits, key=_key, reverse=True)[0]
    return None


if __name__ == "__main__":
    n = find_node()
    print(n or "未找到 node.exe（可设置 NODE 环境变量指定）")
    sys.exit(0 if n else 1)
