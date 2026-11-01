@echo off
rem solo_stream dashboard: starts the local server (skips if already running) and opens the browser.
rem Keep this file ASCII only - double-click runs cmd in code page 949 and UTF-8 Korean breaks the quotes.
rem Closing the "solo_stream dashboard server" window stops the server. If it crashes the window stays open with the error.
setlocal
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONIOENCODING=utf-8"
start "solo_stream dashboard server" /min cmd /k ""%PY%" "%~dp0scripts\dashboard\server.py""
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8767/"
endlocal
