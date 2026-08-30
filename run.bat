@echo off
chcp 65001 >nul
setlocal

REM ---- 申请管理员权限（全局热键必须）----
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 正在申请管理员权限...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [x] 未找到虚拟环境，请先运行 install.bat
    pause
    exit /b 1
)

echo ============================================
echo   游戏实时同声传译 (tscy) 已启动
echo   退出请按  Ctrl + Alt + Q
echo ============================================
echo.

.venv\Scripts\python.exe main.py %*

echo.
echo 程序已退出（错误码 %errorLevel%）
pause
