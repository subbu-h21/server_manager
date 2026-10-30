@echo off
cd /d "%~dp0"
echo =============================================
echo   Server Manager - First Time Setup
echo =============================================
echo.

echo [1/2] Creating Python virtual environment...
python -m venv venv
if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
)
echo Done.
echo.

echo [2/2] Installing Python dependencies...
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)
echo Done.
echo.

echo =============================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Double-click start.bat to run the app
echo =============================================
pause
