@echo off
REM run_ui.bat — Launch MajestyGuard Dynamic Island UI
REM Start AFTER the daemon is running.
REM Python: MG_PYTHON override, else the repo's own .venv, else the known local venvs.
IF NOT "%MG_PYTHON%"=="" (
    SET "VENV=%MG_PYTHON%"
) ELSE IF EXIST "%~dp0..\.venv\Scripts\python.exe" (
    SET "VENV=%~dp0..\.venv\Scripts\python.exe"
) ELSE IF EXIST "C:\tmp\mg-venv-clean\Scripts\python.exe" (
    SET "VENV=C:\tmp\mg-venv-clean\Scripts\python.exe"
) ELSE (
    SET "VENV=C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\python.exe"
)
SET "UI=%~dp0..\ui\main.py"

echo Starting MajestyGuard Dynamic Island and soft-lock shield...
"%VENV%" "%UI%"
