@echo off
REM Setup FRED shortcuts on desktop

setlocal enabledelayedexpansion

set PROJECT_DIR=%~dp0
set DESKTOP=%USERPROFILE%\Desktop
set EXE_PATH=%PROJECT_DIR%dist\FRED.exe

if not exist "!EXE_PATH!" (
    echo ERROR: FRED.exe not found at !EXE_PATH!
    echo Please run: pyinstaller fred.spec
    pause
    exit /b 1
)

echo Setting up FRED on your desktop...

REM Copy GUI shortcut
copy "!EXE_PATH!" "!DESKTOP!\FRED.exe" >nul 2>&1
if !errorlevel! equ 0 (
    echo [OK] FRED GUI copied to desktop
) else (
    echo [ERROR] Failed to copy FRED.exe to desktop
    pause
    exit /b 1
)

REM Copy CLI shortcut
copy "!PROJECT_DIR!fred_cli.bat" "!DESKTOP!\FRED CLI.bat" >nul 2>&1
if !errorlevel! equ 0 (
    echo [OK] FRED CLI shortcut copied to desktop
) else (
    echo [WARNING] Failed to copy CLI shortcut
)

echo.
echo Done! You now have:
echo   - FRED.exe (GUI version)
echo   - FRED CLI.bat (Terminal version)
echo.
pause
