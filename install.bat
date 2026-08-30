@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================
echo   游戏实时同声传译 (tscy) - 环境安装
echo ============================================
echo.

REM ---- 挑选一个"带 tkinter"的解释器（字幕浮层强依赖 tkinter）----
set "PYEXE="
for %%V in (Python314 Python313 Python312 Python311) do (
    if exist "%LOCALAPPDATA%\Programs\Python\%%V\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\%%V\python.exe" -c "import tkinter" >nul 2>&1
        if !errorlevel! equ 0 set "PYEXE=%LOCALAPPDATA%\Programs\Python\%%V\python.exe"
    )
)
if "!PYEXE!"=="" (
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        py -c "import tkinter" >nul 2>&1
        if !errorlevel! equ 0 set "PYEXE=py"
    )
)
if "!PYEXE!"=="" (
    echo [!] 未找到带 tkinter 的 Python，将使用备用解释器
    echo     （字幕浮层需要 tkinter，若启动失败请改装 python.org 官方版）
    set "PYEXE=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)
echo 使用解释器: !PYEXE!

set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"

if not exist "%VPY%" (
    echo [1/3] 创建虚拟环境 .venv ...
    "!PYEXE!" -m venv "%VENV%"
    if errorlevel 1 (
        echo [x] 虚拟环境创建失败
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已存在，跳过
)

echo [2/3] 升级 pip ...
"%VPY%" -m pip install -U pip -q

echo [3/3] 安装依赖（首次较慢，需下载约 400MB）...
"%VPY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [x] 依赖安装失败，可手动执行：
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安装完成
echo ============================================
echo.
echo 下一步：
echo   1. 查看麦克风:  .venv\Scripts\python.exe main.py --list-devices
echo   2. 运行自检:    .venv\Scripts\python.exe main.py --selftest
echo   3. 开始使用:    右键 run.bat -^> 以管理员身份运行
echo.
pause
