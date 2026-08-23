@echo off
REM FRED - the only launcher you need.
REM
REM Starts the voice assistant, which brings up the HUD server quietly in
REM the background. The HUD WINDOW stays closed; click the tray icon to
REM open it.
REM
REM --greet-now is what separates this from the log-on shortcut. Started
REM by hand, the greeting confirms FRED came up, so it speaks within
REM seconds. At log-on (install_startup.py passes no arguments) it waits
REM two minutes instead (GREETING_DELAY_STARTUP, Core/ui/pill_app.py), so
REM it is not talking over everything else Windows is starting.
REM
REM pythonw.exe, not python.exe - no console window; the tray is the UI.

setlocal
set "PY=%~dp0Core\venv\Scripts\pythonw.exe"

if not exist "%PY%" (
  echo Could not find the venv interpreter at:
  echo   "%PY%"
  pause
  exit /b 1
)

start "" "%PY%" "%~dp0fred_popup.py" --greet-now
exit /b 0
