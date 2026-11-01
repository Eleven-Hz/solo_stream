@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10-3.12 from python.org and check "Add to PATH".
    goto fail
)

python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)"
if errorlevel 1 (
    python --version
    echo [ERROR] Python 3.10-3.12 is required.
    goto fail
)

if not exist venv\Scripts\python.exe (
    python -m venv venv
    if errorlevel 1 goto fail
)

venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo Setup complete. Start the app with run.bat
pause
exit /b 0

:fail
echo.
echo [ERROR] Setup failed. Please send the messages above to the developer.
pause
exit /b 1
