@echo off
setlocal
cd /d "%~dp0"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
echo RatanakQuickShare - isolated Windows setup and launch

if not exist "%VENV_PY%" (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python launcher not found. Install Python 3.11 or later from python.org.
    pause
    exit /b 1
  )
  echo Creating a private virtual environment in .venv ...
  py -3 -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo Could not create the virtual environment.
    pause
    exit /b 1
  )
)

if not exist "%VENV_PY%" (
  echo Virtual environment Python was not found.
  pause
  exit /b 1
)

echo Installing dependencies into .venv only ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install dependencies into .venv. Check your connection.
  pause
  exit /b 1
)

echo Starting RatanakQuickShare using .venv ...
"%VENV_PY%" quickshare.py
if errorlevel 1 pause
endlocal
