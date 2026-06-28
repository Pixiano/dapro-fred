@echo off
setlocal enabledelayedexpansion

REM FRED - Personal AI Assistant
REM Double-click this file to launch FRED
REM (This file lives on the Desktop but the project itself lives in a
REM fixed location, so we hardcode the path instead of relying on %~dp0)

set PROJECT_DIR=C:\Users\Dhiraj Vatsal\VatsalDaPro\Projects\Project_FRED
cd /d "%PROJECT_DIR%"

REM Launch FRED GUI (no visible command window)
start "" /b python -u fred_gui.py
