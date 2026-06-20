import time
import sys
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# Disable logging
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 1. Load Data
csv_path = "eurusd_m15_may2026.csv"
df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)
df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

# Convert candles
candle_rows = []
for idx, row in df.iterrows():
    candle_rows.append({
        "time": idx,
        "open": row["Open"],
        "high": row["High"],
        "low": row["Low"],
        "close": row["Close"],
        "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 100,
    })

# Build ticks
rng = np.random.default_rng(42)
ticks_list = []
for c in candle_rows:
    o, h, l, c_price = c["open"], c["high"], c["low"], c["close"]
    t = c["time"]
    n_ticks = 15
    half_spread = 0.00005

    if c_price >= o:
        seg1 = np.linspace(o, l, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(l, h, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(h, c_price, n_ticks - len(seg1) - len(seg2))
    else:
        seg1 = np.linspace(o, h, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(h, l, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(l, c_price, n_ticks - len(seg1) - len(seg2))

    tick_prices = np.concatenate([seg1, seg2, seg3])[:n_ticks]
    spread_jitter = rng.uniform(-0.00001, 0.00001, n_ticks)

    candle_close_time = t + timedelta(minutes=15)
    for i, price in enumerate(tick_prices):
        tick_time = candle_close_time - timedelta(seconds=(n_ticks - 1 - i) * 0.05)
        hs = half_spread + spread_jitter[i]
        ticks_list.append((round(price - hs, 5), round(price + hs, 5), tick_time))

# Patches
import axonai.dataflows.mt5_data as mt5_mod
mt5_mod.mt5_initialize = lambda *a, **kw: True
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._fetch_bars = lambda *a, **kw: candle_rows
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

import axonai.realtime.backtester as bt_mod
bt_mod.mt5_initialize = lambda *a, **kw: True
bt_mod.get_broker_tz_offset = lambda *a, **kw: 2
bt_mod._fetch_bars = lambda *a, **kw: candle_rows
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")

from axonai.realtime.event_types import LiveCandle
bt_mod.BacktestEngine.load_historical_data = lambda self: (
    [LiveCandle(timeframe="M15", open_time=c["time"], open=float(c["open"]), high=float(c["high"]), low=float(c["low"]), close=float(c["close"]), volume=int(c["volume"])) for c in candle_rows],
    ticks_list
)

from axonai.realtime.backtester import BacktestEngine

# Measure execution time
t0 = time.time()
engine = BacktestEngine(ticker="EURUSD=X", days=29)
report = engine.run()
t1 = time.time()
print(f"Backtest run finished in {t1 - t0:.4f} seconds!")
print(f"Total trades: {report['total_trades']}, Win rate: {report['win_rate_percent']:.1f}%, Net profit: {report['net_profit_pips']:.1f} pips")
