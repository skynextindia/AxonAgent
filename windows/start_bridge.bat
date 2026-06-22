@echo off
REM Start the MT5 Bridge Service on Windows
REM The WSL dashboard connects to this bridge for live MT5 data.
echo ============================================================
echo  AxonAI MT5 Bridge Service
echo  Make sure MetaTrader 5 is running and logged in.
echo ============================================================
echo.

set PYTHON=C:\Python313\python.exe
set SCRIPT=%~dp0mt5_bridge.py
set PORT=8765

if not exist "%PYTHON%" (
    echo ERROR: Python not found at %PYTHON%
    echo Please update the PYTHON path in this script.
    pause
    exit /b 1
)

echo Starting MT5 Bridge on port %PORT%...
echo WSL dashboard will connect to this bridge.
echo Close this window to stop the bridge.
echo.

"%PYTHON%" "%SCRIPT%" --port %PORT%

pause
