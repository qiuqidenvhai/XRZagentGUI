@echo off
chcp 65001 >nul
cd /d %~dp0

REM ── 仙人掌 Agent 端到端 GUI 自测启动器 ──
REM 按依赖 (websocket-client) 探测可用 Python，命中后启动 xrz_selftest.py。
REM 探测顺序：XRZ_PYTHON -> PATH 上的 python -> D:\软件\Python\python.exe
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

REM —— 3) D:\软件\Python\python.exe（确认有依赖的那个）——
if not defined RESOLVED (
    if exist "D:\软件\Python\python.exe" set "RESOLVED=D:\软件\Python\python.exe"
)

if not defined RESOLVED (
    if not defined ERRMSG set "ERRMSG=没在 PATH / D:\软件\Python / XRZ_PYTHON 里找到 python.exe。"
    goto :no_python
)

REM —— 探测依赖：websocket-client ——
"%RESOLVED%" -c "import websocket" >nul 2>&1
if errorlevel 1 (
    echo [仙人掌自测] %RESOLVED% 没装 websocket-client，正在安装…
    "%RESOLVED%" -m pip install websocket-client
    if errorlevel 1 (
        set "ERRMSG=安装 websocket-client 失败，请手动：D:\软件\Python\python.exe -m pip install websocket-client"
        goto :no_python
    )
)

echo [仙人掌自测] 使用 Python: %RESOLVED%
echo [仙人掌自测] 输出目录:    C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test
echo.

REM —— 确保测试样本目录存在 ——
if not exist "C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test" (
    mkdir "C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test"
)

REM —— 启动自测（可传 --only T1,T3）——
"%RESOLVED%" xrz_selftest.py %*
set RC=%ERRORLEVEL%

echo.
if %RC% NEQ 0 (
    echo [仙人掌自测] 运行结束，返回码 %RC%。
) else (
    echo [仙人掌自测] 运行结束。报告：C:\Users\X.LAPTOP-CA1GJQE3\Desktop\test\report.md
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