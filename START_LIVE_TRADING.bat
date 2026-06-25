@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI LIVE TRADING SYSTEM - PRODUCTION LAUNCHER
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
echo Dashboard:              http://127.0.0.1:8000
echo Logs:                   C:\Users\rohan\.axonai\logs\axon.log
echo.
echo ════════════════════════════════════════════════════════════════
echo.

REM Start the live trading system
python run.py --direct --symbol EURUSD --live

REM If we get here, the system exited
echo.
echo ════════════════════════════════════════════════════════════════
echo System stopped at %date% %time%
echo ════════════════════════════════════════════════════════════════
echo.
pause
