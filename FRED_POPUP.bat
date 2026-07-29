@echo off
REM Launch FRED's hold-to-talk popup (GUI mode).
REM pythonw.exe so no console window sticks around — the tray icon is the UI.
cd /d "%~dp0"
start "" "%~dp0Core\venv\Scripts\pythonw.exe" "%~dp0fred_popup.py"
