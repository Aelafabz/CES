@echo off
"%~dp0..\..\venv\Scripts\python.exe" "%~dp0dashboard.py" %*
if errorlevel 1 pause
