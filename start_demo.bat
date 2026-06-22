@echo off
echo ========================================================
echo               AxonAI Live Demo Launcher
echo ========================================================
echo.
echo Dual-terminal mode:
echo   FEED  = Exness MT5      (market data)
echo   EXEC  = MetaQuotes MT5  (order execution, via bridge :8766)
echo Both terminals are launched automatically. Make sure each is
echo logged into its broker account.
echo.

set "FEED_PATH=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
set "EXEC_PATH=C:\Program Files\MetaTrader 5\terminal64.exe"

timeout /t 3 /nobreak > nul

echo [1/2] Opening Dashboard in browser...
start http://localhost:8000

echo [2/2] Starting AxonAI Daemon + Execution Bridge...
echo.
python run.py --direct --feed-path "%FEED_PATH%" --exec-path "%EXEC_PATH%"
