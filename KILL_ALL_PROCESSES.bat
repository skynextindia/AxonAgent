@echo off
REM ════════════════════════════════════════════════════════════════
REM   KILL ALL AXONAI PROCESSES
REM   Version: 1.0
REM   Date: 2026-06-26
REM ════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

REM Clear screen
cls

REM Display banner
echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║                                                                ║
echo ║              KILLING ALL PYTHON PROCESSES                     ║
echo ║                                                                ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.

REM Kill all Python processes
echo Terminating all Python processes...
taskkill /IM python.exe /F /T 2>nul

REM Give it a moment
timeout /t 1 /nobreak

REM Check if any Python processes remain
tasklist | find /i "python.exe" >nul
if errorlevel 1 (
    echo.
    echo ✓ SUCCESS: All Python processes terminated
    echo.
) else (
    echo.
    echo ✗ WARNING: Some Python processes may still be running
    echo.
)

echo ════════════════════════════════════════════════════════════════
echo.

pause
