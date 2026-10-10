@echo off
chcp 65001 >nul
cd /d %~dp0

REM ── 仙人掌 Agent 端到端 GUI 自测启动器 ──
REM 按依赖 (websocket-client) 探测可用 Python，命中后启动 xrz_selftest.py。
REM 探测顺序：XRZ_PYTHON -> PATH 上的 python -> 包内 runtime -> 常见安装位置
REM 桌面/项目路径全部动态解析，绝不写死任何用户名（换台电脑照样能跑）。
REM 仙人掌 Agent 必须已通过「启动仙人掌.bat」开启（否则 /health 探不活）。

setlocal EnableExtensions
set "RESOLVED="
set "ERRMSG="

REM —— 1) XRZ_PYTHON ——
if defined XRZ_PYTHON (
    if exist "%XRZ_PYTHON%" (
        set "RESOLVED=%XRZ_PYTHON%"
    ) else (
        set "ERRMSG=XRZ_PYTHON 指向 %XRZ_PYTHON%，但文件不存在。"
    )
)

REM —— 2) PATH 上的 python ——
if not defined RESOLVED (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined RESOLVED set "RESOLVED=%%~fP"
    )
)

REM —— 3) 包内自带 runtime（可迁移版里 runtime\python.exe，优先） ——
if not defined RESOLVED (
    if exist "%~dp0runtime\python.exe" set "RESOLVED=%~dp0runtime\python.exe"
)

REM —— 4) 常见安装位置（换机器也能兜底） ——
if not defined RESOLVED (
    for %%B in (
        "%~dp0runtime\python.exe"
        "D:\软件\Python\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
    ) do (
        if not defined RESOLVED if exist %%B set "RESOLVED=%%~fB"
    )
)

if not defined RESOLVED (
    if not defined ERRMSG set "ERRMSG=没在 PATH / 包内 runtime / 常见安装位置 / XRZ_PYTHON 里找到 python.exe。"
    goto :no_python
)

REM —— 探测依赖：websocket-client ——
"%RESOLVED%" -c "import websocket" >nul 2>&1
if errorlevel 1 (
    echo [仙人掌自测] %RESOLVED% 没装 websocket-client，正在安装…
    "%RESOLVED%" -m pip install websocket-client
    if errorlevel 1 (
        set "ERRMSG=安装 websocket-client 失败，请手动执行：%RESOLVED% -m pip install websocket-client"
        goto :no_python
    )
)

echo [仙人掌自测] 使用 Python: %RESOLVED%

REM —— 【2026-10-06 修"写死桌面位置"】原来这里写死开发者桌面路径，换台电脑
REM    就跑到别人桌面上去、目录还不存在。改为让 Python 按本机真实环境解析
REM    （认 OneDrive 重定向 / 非英文 / 自定义盘），再把结果读回来。
set "DESKTOP_DIR="
for /f "usebackq delims=" %%i in (`"%RESOLVED%" -c "import sys;sys.path.insert(0,'%~dp0');from agent_core.user_paths import desktop_dir;print(desktop_dir(create=True))" 2^>nul`) do set "DESKTOP_DIR=%%i"
if not defined DESKTOP_DIR set "DESKTOP_DIR=%USERPROFILE%\Desktop"
set "TEST_DIR=%DESKTOP_DIR%\test"

echo [仙人掌自测] 输出目录:    %TEST_DIR%
echo.

REM —— 确保测试样本目录存在 ——
if not exist "%TEST_DIR%" (
    mkdir "%TEST_DIR%"
)

REM —— 启动自测（可传 --only T1,T3）——
"%RESOLVED%" xrz_selftest.py %*
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 (
    echo [仙人掌自测] 运行结束，返回码 %RC%。
) else (
    echo [仙人掌自测] 运行结束。报告：%TEST_DIR%\report.md
)
pause
exit /b %RC%

:no_python
echo.
echo [错误] %ERRMSG%
echo        设置 XRZ_PYTHON 指向带 websocket-client 的 python.exe，例如：
echo            set XRZ_PYTHON=D:\软件\Python\python.exe
echo.
pause
exit /b 1