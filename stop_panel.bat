@echo off
REM Stop the gxks panel (kills the whole process tree).
REM Double-click this file, or run: stop_panel.bat
REM 2026-11-23
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_panel.ps1"
echo.
pause
