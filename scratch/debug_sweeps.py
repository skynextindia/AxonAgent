import logging
import sys
from pathlib import Path
from datetime import datetime

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set up logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("debug_sweeps")

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


# 1. Patch VelocityNormalizer.update to handle gaps and backtest decay tick thresholds
import axonai.realtime.velocity_normalizer as vel_mod
from axonai.realtime.velocity_normalizer import NormalizedVelocity

original_vel_update = vel_mod.VelocityNormalizer.update

def patched_vel_update(self, price, timestamp, volume=1.0):
    ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
    dt = ts - self._prev_timestamp if self._prev_timestamp > 0 else 1.0
    
    # Reset peak velocity reference on large tick gaps (e.g. new candle in backtest)
    if dt > 5.0:
        self._peak_velocity = 0.0
        self._peak_decay_ticks = 0

    # Call original update logic
    res = original_vel_update(self, price, timestamp, volume)
    
    # Override is_decaying with backtest threshold (3 ticks)
    decay_ticks_threshold = 3
    is_decaying = res.decay_ratio < 0.5 and self._peak_decay_ticks > decay_ticks_threshold
    
    # Return updated state
    return NormalizedVelocity(
        tick_rate_10s=res.tick_rate_10s,
        tick_rate_60s=res.tick_rate_60s,
        tick_rate_300s=res.tick_rate_300s,
        displacement_velocity=res.displacement_velocity,
        abs_velocity=res.abs_velocity,
        tick_efficiency=res.tick_efficiency,
        acceleration=res.acceleration,
        decay_ratio=res.decay_ratio,
        percentile=res.percentile,
        z_score=res.z_score,
        velocity_ratio=res.velocity_ratio,
        is_unusual=res.is_unusual,
        is_decaying=is_decaying,
        is_accelerating=res.is_accelerating,
        raw_velocity=res.raw_velocity,
    )

vel_mod.VelocityNormalizer.update = patched_vel_update


# 2. Instrument LiquidityEngine.update to print confirmed sweeps
import axonai.realtime.liquidity_engine as liq_mod
original_liq_eval = liq_mod.LiquidityEngine._evaluate_breach

def verbose_liq_eval(self, ls, price, vel, disp, ts):
    was_swept = ls.is_swept
    original_liq_eval(self, ls, price, vel, disp, ts)
    if ls.is_swept and not was_swept:
        logger.info(f"!!! REAL SWEEP CONFIRMED !!! Level: {ls.price} ({ls.level_type}), max_depth={ls.max_breach_depth_pips:.2f}, disp={disp.classification}, decay={vel.decay_ratio:.2f}")

liq_mod.LiquidityEngine._evaluate_breach = verbose_liq_eval


# 3. Patch register_trade to fix case-sensitivity
import axonai.realtime.reversal_model as rev_mod
def patched_register_trade(self, ticket: int, direction: str, entry_price: float, sl: float, tp: float, reason: str = "") -> None:
    import time
    ts = time.time()
    self.health.register_trade(
        ticket, direction, entry_price, ts, 
        self._last_regime_state.regime, self._last_mtf_state.alignment_score
    )
    is_sweep = "sweep" in reason.lower()
    self.exit.register_trade(ticket, direction, entry_price, sl, tp, is_sweep=is_sweep)
    self.phase_tracker.register_trade(
        direction=direction,
        entry_price=entry_price,
        initial_confidence=80.0
    )
    self.entry.reset()

rev_mod.ReversalModel.register_trade = patched_register_trade


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
logger.info("Starting run with fixed velocity normalizer and original sweep scoring...")
report = engine.run()
sweep_trades = sum(1 for t in engine.simulated_trades if "sweep" in t["trigger"])
logger.info(f"Execution complete. Total trades: {report['total_trades']}, Sweep trades: {sweep_trades}, Net P&L: {report['net_profit_pips']:.1f}")

print("\nID | Trigger | Direction | Pips | Close Reason")
print("---|---|---|---|---")
for t in engine.simulated_trades:
    trig = "sweep" if "sweep" in t["trigger"] else "climax"
    print(f"{t['id']} | {trig} | {t['direction']} | {t['pips']:+.1f} | {t['close_reason']}")
