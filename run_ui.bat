@echo off
REM run_ui.bat — Launch MajestyGuard Dynamic Island UI
REM Start AFTER the daemon is running.
SET "VENV=C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\python.exe"
SET "UI=%~dp0ui\main.py"

echo Starting MajestyGuard Dynamic Island and soft-lock shield...
"%VENV%" "%UI%"
