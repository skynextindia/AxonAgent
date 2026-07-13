@echo off
REM Start the MT5 Bridge Service on Windows
REM The WSL dashboard connects to this bridge for live MT5 data.
echo ============================================================
echo  AxonAI MT5 Bridge Service
echo  Make sure MetaTrader 5 is running and logged in.
echo ============================================================
echo.

set PYTHON=python
set SCRIPT=%~dp0mt5_bridge.py
set PORT=8765

echo Starting MT5 Data Bridge on port 8765...
start "MT5 Data Bridge" "%PYTHON%" "%SCRIPT%" --port %PORT%

echo Starting MT5 Execution Bridge on port 8766...
start "MT5 Execution Bridge" "%PYTHON%" "%~dp0execution_bridge.py" --port 8766

pause
