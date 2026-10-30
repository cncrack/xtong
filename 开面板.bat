@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "PANEL_URL=http://127.0.0.1:8301"

echo ==================================================
echo   XXT GXKS panel  ^(port 8301^)
echo ==================================================
echo.

if not exist "%PY%" goto no_venv

:: port already listening? then just open the browser
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',8301);$c.Dispose();exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 goto already_running

echo [..] starting panel in a new window, then opening browser ...
echo      to stop the panel: close the new window titled "XXT GXKS panel"
echo.
start "XXT GXKS panel" "%PY%" -m xxt.cli gxks-panel

powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 120;$i++){ try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',8301);$c.Dispose();$ok=$true}catch{}; if($ok){break}; Start-Sleep -Milliseconds 500 }; if($ok){ Start-Process 'http://127.0.0.1:8301'; Write-Host '[OK] panel ready, browser opened' } else { Write-Host '[WARN] port 8301 not up within 60s - check the panel window for errors' }"

echo.
echo This window can be closed now.
exit /b 0

:already_running
echo [OK] panel already running -^> opening browser
start "" "%PANEL_URL%"
echo.
echo   If the page still looks old, that process runs old code:
echo     stop it first (Ctrl+C in its window), then run this file again.
echo.
exit /b 0

:no_venv
echo [FAIL] not found: %PY%
echo.
echo   This file must sit in the project root, next to .venv
echo   If .venv is missing:
echo       python -m venv .venv
echo       .venv\Scripts\python.exe -m pip install -e .[dev]
echo.
pause
exit /b 2
