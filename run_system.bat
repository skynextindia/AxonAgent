@echo off
title AxonAI Suite Launcher
color 0B
cls

echo ========================================================
echo               AxonAI System Launcher
echo ========================================================
echo.

REM Verify virtual environment
if not exist ".venv\Scripts\python.exe" (
    color 0C
    echo ERROR: Virtual environment not found in .venv\
    echo Please ensure the virtual environment is set up before running.
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ========================================================
echo               AxonAI Suite Launcher
echo ========================================================
echo.
echo  1. Start Daemon - EURUSD only (Dashboard + Engine)
echo  2. Start Daemon - Multi-Pair EURUSD + USDJPY (Dashboard + Engines)
echo  3. Analyze Trade History (MAE / MFE / Drawdown)
echo  4. Run Intraday Backtester (M15 Simulation)
echo  5. Start MT5 Bridge Service (relays to WSL clients on port 8765)
echo  6. Start BOTH: Eightcap (LEAD) + FundingPips (EXEC-NODE, 100k prop-guard)
echo  7. Exit
echo.
echo ========================================================
set /p choice="Select an option (1-7): "

if "%choice%"=="1" goto daemon
if "%choice%"=="2" goto daemon_multi
if "%choice%"=="3" goto analyze
if "%choice%"=="4" goto backtester
if "%choice%"=="5" goto bridge
if "%choice%"=="6" goto dual
if "%choice%"=="7" goto exit
goto menu

:daemon
cls
echo ========================================================
echo  Starting AxonAI Daemon - EURUSD (Single Pair)
echo ========================================================
echo.
echo [1/2] Opening Web Dashboard in browser...
start http://localhost:8000
echo.
echo [2/2] Running daemon engine...
.venv\Scripts\python.exe run.py --direct --symbol EURUSD
echo.
pause
goto menu

:daemon_multi
cls
echo ========================================================
echo  Starting AxonAI Daemon - Multi-Pair (EURUSD + USDJPY)
echo ========================================================
echo.
echo  One daemon thread per pair over a shared MT5 connection.
echo  Correlation engine + per-pair calibration active.
echo.
echo [1/2] Opening Web Dashboard in browser...
start http://localhost:8000
echo.
echo [2/2] Running daemon engines (one thread per pair)...
.venv\Scripts\python.exe run.py --direct --symbols "EURUSD,USDJPY"
echo.
pause
goto menu

:analyze
cls
echo ========================================================
echo  Trade History Analysis - MAE / MFE / Drawdown
echo ========================================================
echo.
echo  Requires the MT5 terminal to be running and logged in.
echo  READ-ONLY: this never opens, modifies, or closes any trade.
echo.
set /p adays="How many days back to analyze (default 30): "
if "%adays%"=="" set adays=30
echo.
echo Analyzing the last %adays% day(s) of closed trades...
echo.
.venv\Scripts\python.exe analyze_trades.py --days %adays%
echo.
pause
goto menu

:backtester
cls
echo ========================================================
echo  Running Intraday Backtest Simulation (EURUSD)
echo ========================================================
echo.
.venv\Scripts\python.exe run_intraday_backtest.py
echo.
echo Backtest completed.
pause
goto menu

:bridge
cls
echo ========================================================
echo  Starting MT5 Bridge Service (Port 8765)
echo ========================================================
echo.
.venv\Scripts\python.exe windows\mt5_bridge.py --port 8765
echo.
pause
goto menu

:dual
cls
echo ========================================================
echo  Starting BOTH terminals — Eightcap (LEAD) + FundingPips
echo ========================================================
echo.
echo  Eightcap  : brain / detection  --^> dashboard http://localhost:8000
echo  FundingPips: exec-node / prop-guard --^> dashboard http://localhost:8001
echo  Decisions relay lead ==^> node over ws://127.0.0.1:8770
echo.
echo  FundingPips uses NO symbol suffix (verified: EURUSD, USDJPY).
echo  Eightcap keeps its .i suffix from default_config.
echo.
echo  Prop-guard on FundingPips 2-Step Pro (100,000 baseline):
echo    - Overall drawdown 6%%       (trips at 4.8%% with 20%% buffer)
echo    - Daily loss      3%%       (trips at 2.4%%)
echo    - Consistency     45%%       (trips at 36%%; blocks new entries only)
echo    - Profit target   6%%       (informational log; no halt)
echo.
start "" http://localhost:8000
start "" http://localhost:8001
echo.
echo [1/2] Launching Eightcap LEAD in a new window...
start "AxonAI - Eightcap LEAD" cmd /k ".venv\Scripts\python.exe run.py --direct --symbols "EURUSD,USDJPY" --mirror-url ws://127.0.0.1:8770"
echo         (waiting 8 seconds for the exec-node port to be available before the follower connects)
timeout /t 8 > nul
echo [2/2] Launching FundingPips EXEC-NODE in a new window...
start "AxonAI - FundingPips EXEC-NODE" cmd /k "set AXONAI_MT5_SYMBOL_SUFFIX=&& .venv\Scripts\python.exe run.py --direct --symbols "EURUSD,USDJPY" --mt5-path "C:\Program Files\MetaTrader 5\terminal64.exe" --port 8001 --exec-node --prop-firm --prop-initial-balance 100000 --prop-max-drawdown-pct 6.0 --prop-daily-loss-pct 3.0"
echo.
echo Both processes launched. Watch each window for startup logs.
echo Watch reports\daemon.log for gate/lock/prop-guard decisions.
echo.
pause
goto menu

:exit
cls
echo Thank you for using AxonAI!
timeout /t 2 > nul
exit /b 0
