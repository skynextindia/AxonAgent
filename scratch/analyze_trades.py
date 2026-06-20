import logging
import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Monkey-patch MT5
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import timedelta

csv_path = "eurusd_m15_may2026.csv"
try:
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
except FileNotFoundError:
    eur = yf.Ticker("EURUSD=X")
    df = eur.history(start="2026-05-01", end="2026-05-30", interval="15m")
    df.to_csv(csv_path)

if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)
df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

import axonai.dataflows.mt5_data as mt5_mod
mt5_mod.mt5_initialize = lambda *a, **kw: True
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

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

def patched_fetch_bars(symbol, timeframe, from_date, to_date):
    return candle_rows

mt5_mod._fetch_bars = patched_fetch_bars

from axonai.realtime.backtester import BacktestEngine
import axonai.realtime.backtester as bt_mod
bt_mod.mt5_initialize = lambda *a, **kw: True
bt_mod.get_broker_tz_offset = lambda *a, **kw: 2
bt_mod._fetch_bars = patched_fetch_bars
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")

# Generate ticks
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

def patched_load_historical_data(self):
    from axonai.realtime.event_types import LiveCandle
    return [LiveCandle("M15", datetime.fromisoformat(c["time"].isoformat()) if hasattr(c["time"], "isoformat") else c["time"], float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), int(c["volume"])) for c in candle_rows], ticks_list

bt_mod.BacktestEngine.load_historical_data = patched_load_historical_data

import axonai.realtime.liquidity_engine as liq_mod

# We override _evaluate_breach to use EXHAUSTION in sweep scoring
def patched_eval(self, ls, price, vel, disp, ts):
    if ls.is_swept or ls.is_broken:
        if ls.max_breach_depth_pips > 15.0 and not ls.is_currently_breached:
            ls.is_swept = False
            ls.is_broken = False
        return

    # SWEEP SCORING (including EXHAUSTION)
    sweep_score = 0.0
    if disp.classification in ("TRAP", "ABSORPTION", "EXHAUSTION"):
        sweep_score += 0.4
    if vel.is_decaying:
        sweep_score += 0.3
    if ls.max_breach_depth_pips < 10.0 and ls.time_since_breach_sec > 5.0:
        sweep_score += 0.2
        
    # BREAK SCORING
    break_score = 0.0
    if disp.classification == "IMPULSE":
        break_score += 0.5
    if ls.max_breach_depth_pips > 8.0:
        break_score += 0.3
    if ls.time_since_breach_sec > 60.0:
        break_score += 0.2

    ls.sweep_probability = min(sweep_score, 1.0)
    ls.acceptance_probability = min(break_score, 1.0)
    
    if ls.sweep_probability >= 0.7:
        ls.is_swept = True
    elif ls.acceptance_probability >= 0.8:
        ls.is_broken = True

liq_mod.LiquidityEngine._evaluate_breach = patched_eval

engine = BacktestEngine(
    ticker="EURUSD=X",
    days=29,
    config={
        "min_signal_quality": 0.60,
        "sl_atr_multiple": 1.0,
        "tp_atr_multiple": 2.5,
        "cooldown_seconds": 300,
        "loss_cooldown_minutes": 30,
        "realtime_velocity_decay_profit_factor": 0.25,
    }
)
report = engine.run()

print("ID | Trigger | Direction | Pips | Close Reason")
print("---|---|---|---|---")
for t in engine.simulated_trades:
    trig = "sweep" if "sweep" in t["trigger"] else "climax"
    print(f"{t['id']} | {trig} | {t['direction']} | {t['pips']:+.1f} | {t['close_reason']}")
