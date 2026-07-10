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
echo  1. Start Live/Dry-Run Daemon (Starts Dashboard + Engine)
echo  2. Run Intraday Backtester (M15 Simulation)
echo  3. Start MT5 Bridge Service (relays to WSL clients on port 8765)
echo  4. Exit
echo.
echo ========================================================
set /p choice="Select an option (1-4): "

if "%choice%"=="1" goto daemon
if "%choice%"=="2" goto backtester
if "%choice%"=="3" goto bridge
if "%choice%"=="4" goto exit
goto menu

:daemon
cls
echo ========================================================
echo  Starting AxonAI Live/Dry-run Daemon
echo ========================================================
echo.
echo [1/2] Opening Web Dashboard in browser...
start http://localhost:8000
echo.
echo [2/2] Running daemon engine...
.venv\Scripts\python.exe run.py --direct
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

:exit
cls
echo Thank you for using AxonAI!
timeout /t 2 > nul
exit /b 0
