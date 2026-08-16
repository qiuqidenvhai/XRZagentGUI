#!/usr/bin/env bash
# 子代理直接测试启动器：先释放被旧后端占用的 DeepSeek 浏览器目录，再跑独立测试
set -u
cd "/d/软件/XianRenZhangAgent" || exit 1

PY="/d/软件/Python/python.exe"
export XRZ_HEADLESS=1
export QT_QPA_PLATFORM=offscreen

# 1) 释放 8888 + DeepSeek 持久化目录（避免两个 Chromium 抢同一 profile 的 SingletonLock）
OLD_PID=$(netstat -ano 2>/dev/null | grep ':8888' | awk '{print $5}' | head -1)
if [ -n "${OLD_PID}" ]; then
  echo "[sub-launcher] 杀掉旧 backend PID=${OLD_PID}"
  taskkill //PID "${OLD_PID}" //F >/dev/null 2>&1 || true
  sleep 3
fi

# 2) 跑子代理测试
echo "[sub-launcher] 运行 xrz_subagent_test.py ..."
"${PY}" xrz_subagent_test.py > xrz_subagent_test.log 2>&1
RC=$?
echo "[sub-launcher] rc=${RC}"
echo "===== xrz_subagent_test.log ====="
cat xrz_subagent_test.log
exit ${RC}
