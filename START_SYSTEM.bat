@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI MASTER SYSTEM LAUNCHER - ONE-CLICK AUTO-START
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

cls
echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║                                                                ║
echo ║          AXONAI ONE-CLICK SYSTEM LAUNCHER - RUNNING            ║
echo ║                                                                ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.

REM Step 1: Kill any active python processes to prevent double-runs
echo [1/3] Terminating duplicate background Python processes...
taskkill /IM python.exe /F /T 2>nul
timeout /t 2 /nobreak >nul

REM Step 2: Spawn both MT5 Bridges in background consoles
echo [2/3] Starting Windows MT5 Data & Execution Bridges...
start "MT5 Bridges" cmd.exe /c "windows\start_bridge.bat"
echo Waiting 5 seconds for MT5 terminal connection to initialize...
timeout /t 5 /nobreak >nul

REM Step 3: Run the main trading loop
echo [3/3] Launching Live Trading Daemons (EURUSD, GBPUSD, USDJPY, AUDUSD, XAUUSD)...
echo.
echo ════════════════════════════════════════════════════════════════
echo system running. Close this window to stop trading.
echo ════════════════════════════════════════════════════════════════
echo.

python run.py --direct --live ^
  --symbol "EURUSD,GBPUSD,USDJPY,AUDUSD,XAUUSD" ^
  --feed-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" ^
  --exec-path "C:\Program Files\MetaTrader 5\terminal64.exe"

REM If exited
echo.
echo System stopped.
pause
