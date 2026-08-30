@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================
echo   同声传译 tscy - 安装程序
echo ============================================
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\tscy"
set "EXE=%INSTALL_DIR%\tscy.exe"
set "ASSETS=%INSTALL_DIR%\assets"

echo 安装路径: %INSTALL_DIR%

if not exist "tscy.exe" (
    echo [x] 未找到 tscy.exe，请确保本 bat 与 tscy.exe 在同一目录
    pause
    exit /b 1
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
echo [1/3] 复制程序文件...
xcopy /Y /I /E /Q "tscy.exe" "%INSTALL_DIR%\" >nul
if exist "assets" xcopy /Y /I /E /Q "assets" "%ASSETS%\" >nul

echo [2/3] 创建桌面快捷方式...
powershell -NoProfile -Command "^
    $s = (New-Object -COM WScript.Shell).CreateShortcut('%USERPROFILE%\Desktop\同声传译 tscy.lnk');^
    $s.TargetPath = '%EXE%';^
    $s.IconLocation = '%ASSETS%\logo.ico';^
    $s.WorkingDirectory = '%INSTALL_DIR%';^
    $s.Save();^
    Write-Host 'OK'^
" 2>nul

echo [3/3] 创建开始菜单快捷方式...
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\tscy"
if not exist "%STARTMENU%" mkdir "%STARTMENU%"
powershell -NoProfile -Command "^
    $s = (New-Object -COM WScript.Shell).CreateShortcut('%STARTMENU%\同声传译 tscy.lnk');^
    $s.TargetPath = '%EXE%';^
    $s.IconLocation = '%ASSETS%\logo.ico';^
    $s.WorkingDirectory = '%INSTALL_DIR%';^
    $s.Save();^
    Write-Host 'OK'^
" 2>nul

echo.
echo ============================================
echo   安装完成
echo ============================================
echo.
echo 已安装到: %INSTALL_DIR%
echo 已创建桌面快捷方式
echo.
echo 首次运行会在 %INSTALL_DIR% 自动下载 Whisper 模型（约 150MB）
echo.
pause
