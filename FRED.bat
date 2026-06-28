@echo off
setlocal enabledelayedexpansion

REM FRED - Personal AI Assistant
REM Double-click this file to launch FRED

set SCRIPT_DIR=%~dp0
cd /d "!SCRIPT_DIR!"

REM Launch FRED GUI (no visible command window)
start "" /b python -u fred_gui.py
