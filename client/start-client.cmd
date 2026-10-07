@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run setup.cmd on this computer first.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0run_client.py" %*
if errorlevel 1 (
    pause
    exit /b 1
)
