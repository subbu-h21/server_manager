@echo off
echo =============================================
echo   Server Manager - First Time Setup
echo =============================================
echo.

echo [1/2] Creating Python virtual environment...
python -m venv venv
echo Done.
echo.

echo [2/2] Installing Python dependencies...
call venv\Scripts\activate
pip install -r requirements.txt
echo Done.
echo.

echo =============================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Double-click start.bat to run the app
echo =============================================
pause
