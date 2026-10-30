@echo off
cd /d "%~dp0"

:: Already running (its startup task runs it in the background)? Then just open its page.
powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1', 8770)).Close(); exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    start "" "http://localhost:8770"
    exit /b 0
)

echo Starting Server Manager...

start "Server Manager" cmd /k "venv\Scripts\activate && python main.py"

:: Open browser once the server is ready
powershell -NoProfile -Command "do { Start-Sleep 1 } until ((Test-NetConnection localhost -Port 8770 -InformationLevel Quiet -WarningAction SilentlyContinue)); Start-Process 'http://localhost:8770'"
