@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI LIVE TRADING - ALTERNATIVE PORT (9000)
REM   Use when port 8000 is already in use
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

cd /d "D:\AXON.AI\AxonAgent-Agy"

cls

echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║                                                                ║
echo ║           AXONAI LIVE TRADING SYSTEM - STARTING               ║
echo ║                                                                ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.
echo Configuration:
echo   • Symbol:              EURUSD
echo   • Mode:                LIVE (Dynamic Position Sizing)
echo   • Feed Terminal:       Exness
echo   • Execution Terminal:  MetaQuotes
echo   • Trading Style:       50+ Trades (Rule A+B Pure-Math)
echo.
echo Dashboard:              http://127.0.0.1:9000
echo Logs:                   C:\Users\rohan\.axonai\logs\axon.log
echo.
echo ════════════════════════════════════════════════════════════════
echo.

python run.py --direct --symbol EURUSD --live --port 9000 ^
  --feed-path "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe" ^
  --exec-path "C:\Program Files\MetaTrader 5\terminal64.exe"

echo.
echo ════════════════════════════════════════════════════════════════
echo System stopped at %date% %time%
echo ════════════════════════════════════════════════════════════════
echo.
pause
