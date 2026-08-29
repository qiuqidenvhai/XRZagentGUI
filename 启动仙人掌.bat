@echo off
chcp 65001 >nul
cd /d %~dp0
REM ── 仙人掌 Agent 启动器（可携带版）──
REM 按依赖 (playwright + PySide6) 探测可用 Python，命中后用同目录 pythonw.exe（无黑窗）启动。
REM 候选解析顺序：
REM   1) 环境变量 XRZ_PYTHON（用户/安装器显式指定）
REM   2) PATH 上的 python / python3（用 where 解析为绝对路径）
REM   3) 常见安装路径：D:\软件\Python\python.exe
REM 任一候选若 import playwright + PySide6 失败就跳过，取首个两项都通过的。
REM 一个都找不到时给出明确提示，让用户去运行「安装依赖.bat」或设置 XRZ_PYTHON。

setlocal EnableExtensions
set "PW="
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

REM —— 2) PATH 上的 python / python3（解析为绝对路径）——
if not defined RESOLVED (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined RESOLVED set "RESOLVED=%%~fP"
    )
)
if not defined RESOLVED (
    for /f "delims=" %%P in ('where python3 2^>nul') do (
        if not defined RESOLVED set "RESOLVED=%%~fP"
    )
)

REM —— 3) 常见安装路径 ——
if not defined RESOLVED (
    if exist "D:\软件\Python\python.exe" set "RESOLVED=D:\软件\Python\python.exe"
)

if not defined RESOLVED (
    if not defined ERRMSG set "ERRMSG=没在 PATH、D:\软件\Python 或 XRZ_PYTHON 里找到 python.exe。"
    goto :no_python
)

REM —— 探测依赖 ——
"%RESOLVED%" -c "import playwright, PySide6" >nul 2>&1
if errorlevel 1 (
    set "ERRMSG=找到了 %RESOLVED%，但它没装齐 playwright 和 PySide6。"
    set "RESOLVED="
    goto :no_python
)

REM —— 选 pythonw.exe（无黑窗），找不到就退回 python.exe ——
for %%P in ("%RESOLVED%") do set "PDIR=%%~dpP"
if exist "%PDIR%pythonw.exe" (
    set "PW=%PDIR%pythonw.exe"
) else (
    set "PW=%RESOLVED%"
)
echo [仙人掌] 使用 Python: %RESOLVED%
echo [仙人掌] 启动器:    %PW%

REM —— 启用 QtWebEngine 远程调试（仅本机 localhost:9222），让 xrz_selftest.py 能用 CDP 驱动真实 DOM ——
set QTWEBENGINE_REMOTE_DEBUGGING=9222

REM —— 确保 启动仙人掌.lnk 存在（带仙人掌图标的任务栏启动器）——
REM 从 .lnk 启动是让 Windows 在任务栏显示仙人掌图标（而非 pythonw 的 Python 蛇标）
REM 的最可靠方式。首次运行会调用 make_shortcut.py 生成，后续跳过。
if not exist "启动仙人掌.lnk" (
    "%RESOLVED%" make_shortcut.py
    if errorlevel 1 (
        echo [警告] 快捷方式生成失败，将直接用 pythonw 启动（任务栏图标可能不是仙人掌）。
    )
)

REM —— 通过 .lnk 启动（脱钩，关窗口不影响后端，且任务栏显示仙人掌图标）——
if exist "启动仙人掌.lnk" (
    start "" "启动仙人掌.lnk"
) else (
    start "" "%PW%" "desktop_app.py"
)
goto :eof

:no_python
echo.
echo [错误] %ERRMSG%
echo        请先双击运行「安装依赖.bat」，或者设置 XRZ_PYTHON 指向已装好依赖的 python.exe，例如：
echo            set XRZ_PYTHON=D:\软件\Python\python.exe
echo.
pause
exit /b 1