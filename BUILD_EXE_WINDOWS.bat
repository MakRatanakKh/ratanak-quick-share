@echo off
setlocal
cd /d "%~dp0"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
echo RatanakQuickShare - isolated Windows executable build

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

echo Installing build dependencies into .venv only ...
"%VENV_PY%" -m pip install -r requirements-build.txt
if errorlevel 1 (
  echo Failed to install build dependencies into .venv.
  pause
  exit /b 1
)

"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name RatanakQuickShare --add-data "assets;assets" quickshare.py
if errorlevel 1 (
  echo The build failed.
  pause
  exit /b 1
)

echo.
echo Your app is here: dist\RatanakQuickShare.exe
pause
endlocal
