@echo off
REM ════════════════════════════════════════════════════════════════
REM   AXONAI TRADING SYSTEM - MASTER LAUNCHER
REM   Version: 1.0
REM   Date: 2026-06-26
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

:menu
cls
echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║                                                                ║
echo ║           AXONAI TRADING SYSTEM - LAUNCHER MENU               ║
echo ║                                                                ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.
echo Select an option:
echo.
echo   [1] START LIVE TRADING (50+ Trades, Real Demo Orders)
echo   [2] START PAPER TRADING (Safe Test Mode, Simulated Orders)
echo   [3] KILL ALL PROCESSES
echo   [4] VIEW LIVE LOGS
echo   [5] EXIT
echo.
set /p choice="Enter your choice (1-5): "

if "%choice%"=="1" goto live
if "%choice%"=="2" goto paper
if "%choice%"=="3" goto kill
if "%choice%"=="4" goto logs
if "%choice%"=="5" goto exit
echo Invalid choice. Please try again.
timeout /t 2 /nobreak
goto menu

:live
cls
echo Starting LIVE TRADING SYSTEM...
echo.
cd /d "D:\AXON.AI\AxonAgent-Agy"
python run.py --direct --symbol EURUSD --live
goto menu

:paper
cls
echo Starting PAPER TRADING SYSTEM...
echo.
cd /d "D:\AXON.AI\AxonAgent-Agy"
python run.py --direct --symbol EURUSD --paper
goto menu

:kill
cls
echo Terminating all Python processes...
taskkill /IM python.exe /F /T 2>nul
timeout /t 1 /nobreak
echo All processes terminated.
timeout /t 2 /nobreak
goto menu

:logs
cls
echo Opening live logs...
echo.
type "C:\Users\rohan\.axonai\logs\axon.log" | more
goto menu

:exit
exit /b 0
