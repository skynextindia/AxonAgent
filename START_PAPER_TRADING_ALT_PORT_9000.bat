@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI PAPER TRADING - ALTERNATIVE PORT (9000)
REM   Use when port 8000 is already in use (safe test mode)
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

cd /d "%~dp0"

cls

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
echo Dashboard:              http://127.0.0.1:9000
echo Logs:                   C:\Users\rohan\.axonai\logs\axon.log
echo.
echo ════════════════════════════════════════════════════════════════
echo.

python run.py --direct --paper --port 9000 ^
  --feed-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" ^
  --exec-path "C:\Program Files\MetaTrader 5\terminal64.exe"

echo.
echo ════════════════════════════════════════════════════════════════
echo System stopped at %date% %time%
echo ════════════════════════════════════════════════════════════════
echo.
pause
