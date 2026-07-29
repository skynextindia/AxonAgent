@echo off
echo ========================================================
echo               AxonAI Live Demo Launcher
echo ========================================================
echo.
echo Make sure MetaTrader 5 is open and logged into your broker.
echo.

REM Use the SAME interpreter as run_system.bat. A bare "python" here resolves to
REM system Python (Python311), which starts a SECOND daemon on a different
REM interpreter that races the .venv one for ports 8000/8001/8770 — the exact
REM cause of stale/duplicate processes and the flaky dashboard on restart.
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found. Run from the project root with the venv set up.
    pause
    exit /b 1
)

timeout /t 3 /nobreak > nul

echo [1/2] Opening Dashboard in browser...
start http://localhost:8000

echo [2/2] Starting AxonAI Daemon and Web Server...
echo.
.venv\Scripts\python.exe run.py --direct
