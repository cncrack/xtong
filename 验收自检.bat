@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

echo ==================================================
echo   XXT GXKS  -  self check  ^(run after code change^)
echo ==================================================
echo.

if not exist "%PY%" goto no_venv

echo [1/2] pytest ...
"%PY%" -m pytest -q
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" goto test_failed
echo [OK] all tests passed
echo.

echo [2/2] page JS syntax check ...
"%PY%" -c "from xxt.panel_gxks.page import PAGE; import re,pathlib,sys; m=re.search(r'<script>(.*)</script>', PAGE, re.S); sys.exit('no <script> block') if not m else None; p=pathlib.Path('data/debug/_page.js'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(m.group(1), encoding='utf-8'); print('exported', p, len(m.group(1)), 'chars')"
if errorlevel 1 goto js_export_failed

where node >nul 2>&1
if errorlevel 1 goto no_node
node --check data\debug\_page.js
if errorlevel 1 goto js_syntax_failed
echo [OK] page JS syntax ok
goto all_green

:no_node
echo [SKIP] node not found - skipping node --check ^(pytest already covers page asserts^)
goto all_green

:all_green
echo.
echo ==================================================
echo   ALL GREEN - now double-click the launcher bat
echo ==================================================
exit /b 0

:test_failed
echo [FAIL] tests failed ^(exit code %RC%^) - see the traceback above
echo.
pause
exit /b %RC%

:js_export_failed
echo [FAIL] could not export page JS
echo.
pause
exit /b 1

:js_syntax_failed
echo [FAIL] page JS has a syntax error ^(unbalanced brackets in string concat^)
echo.
pause
exit /b 1

:no_venv
echo [FAIL] not found: %PY%
echo.
echo   This file must sit in the project root, next to .venv
echo.
pause
exit /b 2
