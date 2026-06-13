@echo off
REM run_daemon.bat - MajestyGuard v2 Python Daemon
SET "VENV=C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\python.exe"
FOR %%I IN ("%~dp0..\..") DO SET "MG_MAIN_ROOT=%%~fI"
IF "%MG_V2_ROOT%"=="" SET "MG_V2_ROOT=%MG_MAIN_ROOT%\active\MajestyGuard-v2"
SET "DAEMON=%MG_V2_ROOT%\daemon\main.py"
SET "POLICY_AUDIT=%MG_V2_ROOT%\daemon\mg_policy_audit.py"

REM Locking: set to 1 only when ready for real lock behavior.
SET MG_ENABLE_LOCK=0

REM WHCDF is blocked on this local package, so keep its IPC bridge off by default.
REM Set to 1 only when testing a provisioned WHCDF companion.
SET MG_ENABLE_WHCDF_IPC=0

REM Optional dry-run controls. Default manual runs stop after 10 minutes.
REM Unbounded runs are blocked by the policy audit; set MG_MAX_SECONDS or MG_MAX_FRAMES.
IF "%MG_MAX_SECONDS%"=="" SET MG_MAX_SECONDS=600
IF "%MG_MAX_FRAMES%"=="" SET MG_MAX_FRAMES=0
IF "%MG_LOG_EVERY_N_FRAMES%"=="" SET MG_LOG_EVERY_N_FRAMES=30
IF "%MG_RECOGNITION_THRESHOLD%"=="" SET MG_RECOGNITION_THRESHOLD=0.78
IF "%MG_ACTIVE_RECOGNITION_THRESHOLD%"=="" SET MG_ACTIVE_RECOGNITION_THRESHOLD=0.65
IF "%MG_STRANGER_SCORE_THRESHOLD%"=="" SET MG_STRANGER_SCORE_THRESHOLD=0.55
IF "%MG_SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES%"=="" SET MG_SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES=15
IF "%MG_SCANNING_OWNER_AMBIGUITY_MIN_SCORE%"=="" SET MG_SCANNING_OWNER_AMBIGUITY_MIN_SCORE=0.50
IF "%MG_SCANNING_OWNER_AMBIGUITY_PRESENCE%"=="" SET MG_SCANNING_OWNER_AMBIGUITY_PRESENCE=0.65
IF "%MG_LIVENESS_THRESHOLD%"=="" SET MG_LIVENESS_THRESHOLD=0.70
IF "%MG_ACTIVE_LIVENESS_JITTER_FLOOR%"=="" SET MG_ACTIVE_LIVENESS_JITTER_FLOOR=0.55
IF "%MG_ADAFACE_FLIP_FUSION%"=="" SET MG_ADAFACE_FLIP_FUSION=1

echo Running offline MajestyGuard policy audit...
"%VENV%" "%POLICY_AUDIT%" --require-bound
IF ERRORLEVEL 1 (
    echo.
    echo Policy audit failed. Daemon and camera were not started.
    exit /b 1
)

echo Starting MajestyGuard daemon...
echo.
echo ============================================================
echo CAMERA / RECOGNITION STARTING IN 8 SECONDS
echo Sit centered, face the webcam, and keep good lighting.
echo ============================================================
echo.
timeout /t 8 /nobreak >nul
"%VENV%" "%DAEMON%"
