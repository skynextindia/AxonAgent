import time
import sys
import logging
from pathlib import Path

# Disable all debug logging during speed test
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_intraday_backtest import BacktestEngine, candle_rows, ticks_list, bt_mod

start_time = time.time()
engine = BacktestEngine(ticker="EURUSD=X", days=29)
report = engine.run()
elapsed = time.time() - start_time
print(f"Backtest completed in {elapsed:.4f} seconds!")
print(f"Total trades: {report['total_trades']}, Win rate: {report['win_rate_percent']}%")
