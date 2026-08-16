#!/bin/bash
cd "/d/软件/XianRenZhangAgent"
export XRZ_HEADLESS=1
export QT_QPA_PLATFORM=offscreen
export QTWEBENGINE_REMOTE_DEBUGGING=9222
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

# 启动后端
echo "启动后端..."
"D:/软件/Python/python.exe" terminal.py > xrz_backend.log 2>&1 &
echo "backend pid=$!"

# 等待后端就绪
for i in $(seq 1 20); do
  sleep 2
  if curl -s http://127.0.0.1:8888/health >/dev/null 2>&1; then
    echo "后端就绪（$((i*2))s）"
    break
  fi
  echo "等待后端... ${i}/20"
done

# 跑测试
echo "开始完整测试..."
"D:/软件/Python/python.exe" xrz_func_test.py > xrz_func_test.log 2>&1
echo "测试完成"
