@echo off
echo ========================================================
echo               AxonAI Live Demo Launcher
echo ========================================================
echo.
echo Make sure MetaTrader 5 is open and logged into your broker.
echo.
timeout /t 3 /nobreak > nul

echo [1/2] Opening Dashboard in browser...
start http://localhost:8000

echo [2/2] Starting AxonAI Daemon and Web Server...
echo.
python run.py --direct
