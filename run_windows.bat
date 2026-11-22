@echo off
rem Double-click this file to start BigSheet.
rem The first run creates a private Python environment in .venv and installs dependencies.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/windows/
  echo Tick "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting up, this takes a minute...
  %PY% -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt || goto :fail
)
start "" .venv\Scripts\pythonw main.py %*
exit /b 0

:fail
echo Setup failed. See the messages above.
pause
exit /b 1
