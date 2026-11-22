@echo off
rem Builds dist\BigSheet\BigSheet.exe, a standalone app that does not need Python installed.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
if not exist .venv %PY% -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt "pyinstaller>=6.10" || goto :fail
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --name BigSheet ^
  --collect-data duckdb_extension_httpfs ^
  main.py || goto :fail
echo.
echo Built dist\BigSheet\BigSheet.exe. Copy the whole dist\BigSheet folder to keep it working.
pause
exit /b 0
:fail
echo Build failed. See the messages above.
pause
exit /b 1
