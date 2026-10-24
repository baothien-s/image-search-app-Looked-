@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python --version >nul 2>&1
    if errorlevel 1 goto missing
    python -m venv .venv
)
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto failed
if not exist .env copy .env.example .env >nul
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:missing
echo Install Python 3.11 or newer from https://www.python.org/downloads/ .
echo Enable Add Python to PATH, reopen the terminal, and run start.bat again.
pause
exit /b 1
:failed
echo Looked could not start. See the README troubleshooting steps.
pause
exit /b 1
