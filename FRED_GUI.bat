@echo off
REM FRED GUI Application Launcher
REM Double-click this file to launch FRED with a graphical interface

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "!SCRIPT_DIR!"

REM Add project to Python path
set PYTHONPATH=!SCRIPT_DIR!Core;!PYTHONPATH!

REM Try to run the GUI
python fred_gui.py

REM If it fails, show the error
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Failed to launch FRED GUI
    echo.
    echo Troubleshooting:
    echo 1. Make sure Python 3.10+ is installed and in PATH
    echo 2. Check that all dependencies are installed: pip install -r requirements.txt
    echo 3. Check internet connection for model downloads
    echo.
    echo Try running from command line for more details:
    echo   python fred_gui.py
    echo.
    pause
    exit /b 1
)
