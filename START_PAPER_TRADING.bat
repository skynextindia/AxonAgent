@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI PAPER TRADING SYSTEM - SAFE TEST MODE
REM   Version: 1.0
REM   Date: 2026-06-26
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

REM Change to the project directory
cd /d "D:\AXON.AI\AxonAgent-Agy"

REM Clear screen
cls

REM Display banner
echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║                                                                ║
echo ║        AXONAI PAPER TRADING SYSTEM - SAFE TEST MODE           ║
echo ║                                                                ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.
echo Configuration:
echo   • Symbol:              MULTICURRENCY (5 Pairs + Gold)
echo   • Mode:                PAPER (Simulated fills, no real orders)
echo   • Feed Terminal:       Exness
echo   • Execution Terminal:  MetaQuotes
echo   • Trading Style:       50+ Trades (Rule A+B Pure-Math)
echo.
echo IMPORTANT: No real orders will be placed. All fills are simulated.
echo Use this mode for testing strategies without market risk.
echo.
echo Dashboard:              http://127.0.0.1:8000
echo Logs:                   C:\Users\rohan\.axonai\logs\axon.log
echo.
echo ════════════════════════════════════════════════════════════════
echo.

REM Start the paper trading system with dual terminals
REM Feed: Exness (data) | Execution: MetaQuotes (simulated)
python run.py --direct --paper ^
  --feed-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" ^
  --exec-path "C:\Program Files\MetaTrader 5\terminal64.exe"

REM If we get here, the system exited
echo.
echo ════════════════════════════════════════════════════════════════
echo System stopped at %date% %time%
echo ════════════════════════════════════════════════════════════════
echo.
pause
