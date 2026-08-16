@echo off
chcp 65001 >nul
cd /d %~dp0
REM ── 仙人掌 Agent 依赖安装器 ──
REM 1) 安装 Python 依赖（playwright / PySide6 / python-docx / python-pptx ...）
REM 2) 把 Chromium 浏览器二进制下载到 D 盘项目目录（与 xrz_paths 一致）
REM 3) 安装本地 Ollama 集成（可选，缺则跳过）

set "XRZ_PY="
where python >nul 2>&1 && set "XRZ_PY=python"
if not defined XRZ_PY ( where python3 >nul 2>&1 && set "XRZ_PY=python3" )
if defined XRZ_PYTHON set "XRZ_PY=%XRZ_PYTHON%"
if not defined XRZ_PY (
    echo [错误] 未找到 Python。请先安装 Python 3.10+ 并加入 PATH。
    pause
    exit /b 1
)

echo [1/2] 安装 Python 依赖 ...
%XRZ_PY% -m pip install --upgrade pip
%XRZ_PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [警告] 部分依赖安装失败，可重试或检查网络。
)

echo [2/2] 下载 Chromium 浏览器（首次较慢，约 100~150MB）...
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0xrz_data\playwright_browsers"
%XRZ_PY% -m playwright install chromium
if errorlevel 1 (
    echo [警告] Chromium 下载失败，请检查网络后重试。
)

echo.
echo 完成！现在双击「启动仙人掌.bat」即可运行。
pause
