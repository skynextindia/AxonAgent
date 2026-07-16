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

REM Dual-terminal isolation: data = Exness, execution = MetaQuotes.
REM Always pass --path; without it each bridge attaches to whichever
REM terminal MT5 finds first (account + orders can route to the wrong one).
set DATA_TERMINAL=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe
set TRADE_TERMINAL=C:\Program Files\MetaTrader 5\terminal64.exe

echo Starting MT5 Data Bridge on port 8765 (Exness)...
start "MT5 Data Bridge" "%PYTHON%" "%SCRIPT%" --port %PORT% --path "%DATA_TERMINAL%"

echo Starting MT5 Execution Bridge on port 8766 (MetaQuotes)...
start "MT5 Execution Bridge" "%PYTHON%" "%~dp0execution_bridge.py" --port 8766 --path "%TRADE_TERMINAL%"

pause
