import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axonai.realtime.backtester import BacktestEngine
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from axonai.realtime.event_types import LiveCandle

# Fetch M15 data
csv_path = "eurusd_m15_may2026.csv"
df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)
df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

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
bt_mod._fetch_bars = lambda *a, **kw: candle_rows
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
bt_mod.BacktestEngine.load_historical_data = lambda self: (
    [LiveCandle(timeframe="M15", open_time=c["time"], open=float(c["open"]), high=float(c["high"]), low=float(c["low"]), close=float(c["close"]), volume=int(c["volume"])) for c in candle_rows],
    ticks_list
)

config = {
    "min_signal_quality": 0.60,
    "sl_atr_multiple": 1.0,
    "tp_atr_multiple": 2.5,
    "cooldown_seconds": 300,
    "loss_cooldown_minutes": 30,
    "realtime_velocity_decay_profit_factor": 0.25,
    "backtest_mode": True
}

engine1 = BacktestEngine(ticker="EURUSD=X", days=29, config=config)
report1 = engine1.run()
print("Run 1 - Total Trades:", report1["total_trades"])
print("Run 1 - Win Rate:", report1["win_rate_percent"])
print("Run 1 - Net Profit:", report1["net_profit_pips"])

engine2 = BacktestEngine(ticker="EURUSD=X", days=29, config=config)
report2 = engine2.run()
print("Run 2 - Total Trades:", report2["total_trades"])
print("Run 2 - Win Rate:", report2["win_rate_percent"])
print("Run 2 - Net Profit:", report2["net_profit_pips"])


