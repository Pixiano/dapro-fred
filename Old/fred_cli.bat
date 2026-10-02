@echo off
REM FRED CLI Launcher
REM Run this to launch FRED as a command-line application

cd /d "%~dp0"
python Core\main.py
pause
