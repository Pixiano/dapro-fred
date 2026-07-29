@echo off
REM FRED GUI Launcher
REM Run this to launch FRED as the always-on-top Siri-style overlay

cd /d "%~dp0"
start "" /b python -u fred_overlay.py
