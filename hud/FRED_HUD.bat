@echo off
REM ---------------------------------------------------------------------
REM  FRED HUD launcher.
REM
REM    FRED_HUD.bat            start the server if it isn't up, open Chrome
REM    FRED_HUD.bat restart    kill the server holding :8777, then start
REM    FRED_HUD.bat stop       kill the server holding :8777, don't reopen
REM
REM  Server shutdown is always PID-specific: the PID is read out of netstat
REM  for this exact port and killed by number. Never `taskkill /IM python*`
REM  -- FRED itself runs on python and pattern-killing would take it down
REM  as collateral.
REM ---------------------------------------------------------------------
setlocal enabledelayedexpansion

set "PORT=8777"
set "HERE=%~dp0"
set "PY=%HERE%..\Core\venv\Scripts\pythonw.exe"
set "PYC=%HERE%..\Core\venv\Scripts\python.exe"
set "URL=http://127.0.0.1:%PORT%/"
set "PROFILE=%TEMP%\fred-hud-profile"

if not exist "%PY%" (
  echo [hud] venv python not found at "%PY%"
  echo [hud] falling back to python on PATH
  set "PY=pythonw"
  set "PYC=python"
)

REM ---- find whoever owns the port (empty if free) ----------------------
set "PID="
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /r /c:":%PORT% .*LISTENING"') do set "PID=%%p"

if /i "%~1"=="stop" goto :kill
if /i "%~1"=="restart" goto :kill
goto :start

:kill
if defined PID (
  echo [hud] stopping server on :%PORT% ^(pid %PID%^)
  taskkill /PID %PID% /F >nul 2>&1
  set "PID="
  REM give the socket a moment to actually release before rebinding
  ping -n 2 127.0.0.1 >nul
) else (
  echo [hud] nothing listening on :%PORT%
)
if /i "%~1"=="stop" goto :end

:start
if defined PID (
  echo [hud] server already up on :%PORT% ^(pid %PID%^)
) else (
  echo [hud] starting server on :%PORT%
  start "" /b "%PY%" "%HERE%server.py"
  REM wait for the port to accept before pointing a browser at it
  set "READY="
  for /l %%i in (1,1,20) do (
    if not defined READY (
      ping -n 2 127.0.0.1 >nul
      for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /r /c:":%PORT% .*LISTENING"') do set "READY=%%p"
    )
  )
  if not defined READY (
    echo [hud] server did not come up -- run this to see the error:
    echo        "%PYC%" "%HERE%server.py"
    goto :end
  )
  echo [hud] up ^(pid !READY!^)
)

REM ---- Chrome in kiosk, throwaway profile ------------------------------
set "CHROME="
for %%c in (
  "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if not defined CHROME if exist %%c set "CHROME=%%~c"

if not defined CHROME (
  echo [hud] Chrome not found -- opening in the default browser instead
  start "" "%URL%"
  goto :end
)

echo [hud] opening kiosk
start "" "%CHROME%" --kiosk --user-data-dir="%PROFILE%" --no-first-run ^
  --disable-features=Translate --autoplay-policy=no-user-gesture-required "%URL%"

:end
endlocal
