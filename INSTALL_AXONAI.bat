@echo off
REM ════════════════════════════════════════════════════════════════════════════════
REM   AXONAI TRADING SYSTEM - AUTOMATED INSTALLATION SCRIPT
REM   Version: 1.0
REM   Date: 2026-06-26
REM   Platform: Windows 10/11
REM ════════════════════════════════════════════════════════════════════════════════
REM
REM   This script will:
REM   1. Check Python version (3.10+)
REM   2. Clone/update AxonAI repository
REM   3. Create Python virtual environment
REM   4. Install dependencies
REM   5. Configure MT5 terminals
REM   6. Create batch shortcuts
REM   7. Verify installation
REM
REM ════════════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

REM Colors and formatting
cls
color 0A

echo.
echo ╔════════════════════════════════════════════════════════════════════════════════╗
echo ║                                                                                ║
echo ║         AXONAI TRADING SYSTEM - AUTOMATED INSTALLATION                        ║
echo ║         Version: 0.2.5                                                        ║
echo ║                                                                                ║
echo ╚════════════════════════════════════════════════════════════════════════════════╝
echo.
echo This script will set up AxonAI on your machine.
echo.
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 1. CHECK PREREQUISITES
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 1: CHECKING PREREQUISITES ═══════════════════════════════════════════╗
echo.

REM Check Python
echo [*] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] ERROR: Python is not installed or not in PATH
    echo.
    echo Please install Python 3.10 or higher from:
    echo   https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [+] Python found: %PYTHON_VERSION%

REM Check Git
echo [*] Checking Git installation...
git --version >nul 2>&1
if errorlevel 1 (
    echo [!] WARNING: Git is not installed
    echo Please install Git from: https://git-scm.com/download/win
    echo.
    pause
)
for /f "tokens=3" %%i in ('git --version 2^>^&1') do set GIT_VERSION=%%i
echo [+] Git found: %GIT_VERSION%

REM Check pip
echo [*] Checking pip...
pip --version >nul 2>&1
if errorlevel 1 (
    echo [!] ERROR: pip is not available
    pause
    exit /b 1
)
echo [+] pip is available
echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 2. CLONE OR UPDATE REPOSITORY
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 2: SETTING UP REPOSITORY ════════════════════════════════════════════╗
echo.

set REPO_URL=https://github.com/skynextindia/AxonAgent.git
set INSTALL_DIR=%USERPROFILE%\AxonAI

echo [*] Installation directory: %INSTALL_DIR%
echo.

if exist "%INSTALL_DIR%\.git" (
    echo [*] Repository exists. Pulling latest changes...
    cd /d "%INSTALL_DIR%"
    git fetch origin
    git checkout velocity
    git pull origin velocity
    if errorlevel 1 (
        echo [!] ERROR: Failed to pull latest changes
        pause
        exit /b 1
    )
    echo [+] Repository updated to latest
) else (
    if exist "%INSTALL_DIR%" (
        echo [!] Directory exists but not a git repo. Removing...
        rmdir /s /q "%INSTALL_DIR%"
    )
    echo [*] Cloning repository from GitHub...
    git clone -b velocity %REPO_URL% "%INSTALL_DIR%"
    if errorlevel 1 (
        echo [!] ERROR: Failed to clone repository
        pause
        exit /b 1
    )
    cd /d "%INSTALL_DIR%"
    echo [+] Repository cloned successfully
)

echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 3. CREATE VIRTUAL ENVIRONMENT
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 3: CREATING PYTHON VIRTUAL ENVIRONMENT ════════════════════════════╗
echo.

set VENV_DIR=%INSTALL_DIR%\venv

if exist "%VENV_DIR%" (
    echo [*] Virtual environment already exists. Skipping...
) else (
    echo [*] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [!] ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [+] Virtual environment created
)

echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 4. INSTALL DEPENDENCIES
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 4: INSTALLING DEPENDENCIES ══════════════════════════════════════════╗
echo.
echo This may take 5-10 minutes...
echo.

call "%VENV_DIR%\Scripts\activate.bat"

echo [*] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [!] WARNING: pip upgrade had issues, continuing...
)

echo [*] Installing AxonAI and dependencies...
cd /d "%INSTALL_DIR%"
pip install -e .
if errorlevel 1 (
    echo [!] ERROR: Failed to install dependencies
    pause
    exit /b 1
)
echo [+] Dependencies installed successfully

deactivate

echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 5. CREATE BATCH SHORTCUTS
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 5: CREATING BATCH SHORTCUTS ══════════════════════════════════════════╗
echo.
echo [*] Creating shortcuts for easy system startup...
echo.

REM Create LAUNCHER batch
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo call venv\Scripts\activate.bat
    echo python run.py --direct --symbol EURUSD --live
    echo pause
) > "%INSTALL_DIR%\RUN_LIVE.bat"
echo [+] Created: RUN_LIVE.bat

REM Create PAPER batch
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo call venv\Scripts\activate.bat
    echo python run.py --direct --symbol EURUSD --paper
    echo pause
) > "%INSTALL_DIR%\RUN_PAPER.bat"
echo [+] Created: RUN_PAPER.bat

REM Create alternative port batch
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo call venv\Scripts\activate.bat
    echo python run.py --direct --symbol EURUSD --live --port 9000
    echo pause
) > "%INSTALL_DIR%\RUN_LIVE_PORT_9000.bat"
echo [+] Created: RUN_LIVE_PORT_9000.bat

echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 6. VERIFY INSTALLATION
REM ────────────────────────────────────────────────────────────────────────────────

cls
echo ╔═══ STEP 6: VERIFYING INSTALLATION ════════════════════════════════════════════╗
echo.

call "%VENV_DIR%\Scripts\activate.bat"
cd /d "%INSTALL_DIR%"

echo [*] Checking Python modules...
python -c "import axonai; print('[+] AxonAI module found')" 2>nul
if errorlevel 1 (
    echo [!] WARNING: Could not import axonai module
)

python -c "import MetaTrader5; print('[+] MetaTrader5 module found')" 2>nul
if errorlevel 1 (
    echo [!] WARNING: MetaTrader5 not installed - required for live trading
)

python -c "import fastapi; print('[+] FastAPI module found')" 2>nul
if errorlevel 1 (
    echo [!] ERROR: FastAPI not found
)

python -c "import pandas; print('[+] Pandas module found')" 2>nul
if errorlevel 1 (
    echo [!] ERROR: Pandas not found
)

deactivate

echo.
echo ════════════════════════════════════════════════════════════════════════════════
pause

REM ────────────────────────────────────────────────────────────────────────────────
REM 7. FINAL INSTRUCTIONS
REM ────────────────────────────────────────────────────────────────────────────────

cls
color 0B

echo.
echo ╔════════════════════════════════════════════════════════════════════════════════╗
echo ║                                                                                ║
echo ║                   INSTALLATION COMPLETE! ✓                                    ║
echo ║                                                                                ║
echo ╚════════════════════════════════════════════════════════════════════════════════╝
echo.
echo Installation Directory: %INSTALL_DIR%
echo.
echo ════════════════════════════════════════════════════════════════════════════════
echo NEXT STEPS:
echo ════════════════════════════════════════════════════════════════════════════════
echo.
echo 1. CONFIGURE MT5 TERMINALS (IMPORTANT):
echo    - Install MetaTrader 5 Exness: https://www.exness.com/
echo    - Install MetaTrader 5 MetaQuotes: https://www.metatrader5.com/
echo    - Note the installation paths
echo.
echo 2. RUN THE SYSTEM:
echo    Option A - Using batch shortcuts (easiest):
echo      • Double-click: %INSTALL_DIR%\RUN_LIVE.bat (live trading)
echo      • Double-click: %INSTALL_DIR%\RUN_PAPER.bat (safe test mode)
echo      • Double-click: %INSTALL_DIR%\RUN_LIVE_PORT_9000.bat (if port 8000 in use)
echo.
echo    Option B - Using command line:
echo      • cmd /k cd /d "%INSTALL_DIR%" ^&^& venv\Scripts\activate.bat
echo      • python run.py --direct --symbol EURUSD --live
echo.
echo 3. ACCESS DASHBOARD:
echo    • Open browser: http://127.0.0.1:8000
echo    • For port 9000: http://127.0.0.1:9000
echo.
echo 4. LOGS:
echo    • Location: %USERPROFILE%\.axonai\logs\axon.log
echo    • Check logs if system doesn't start
echo.
echo 5. TROUBLESHOOTING:
echo    • MetaTrader5 errors? Check MT5 terminal is running
echo    • Port in use? Use RUN_LIVE_PORT_9000.bat or different port
echo    • Import errors? Run this installer again
echo.
echo ════════════════════════════════════════════════════════════════════════════════
echo.
pause
exit /b 0
