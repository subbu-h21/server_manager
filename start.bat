@echo off
echo Starting Server Manager...

start "Server Manager" cmd /k "venv\Scripts\activate && python main.py"

:: Open browser once the server is ready
powershell -NoProfile -Command "do { Start-Sleep 1 } until ((Test-NetConnection localhost -Port 8002 -InformationLevel Quiet -WarningAction SilentlyContinue)); Start-Process 'http://localhost:8002'"
