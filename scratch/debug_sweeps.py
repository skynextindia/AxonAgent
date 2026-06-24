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


# 1. Patch VelocityNormalizer
import axonai.realtime.velocity_normalizer as vel_mod
from axonai.realtime.velocity_normalizer import NormalizedVelocity

original_vel_update = vel_mod.VelocityNormalizer.update

def patched_vel_update(self, price, timestamp, volume=1.0):
    ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
    dt = ts - self._prev_timestamp if self._prev_timestamp > 0 else 1.0
    
    if dt > 5.0:
        self._peak_velocity = 0.0
        self._peak_decay_ticks = 0

    res = original_vel_update(self, price, timestamp, volume)
    
    decay_ticks_threshold = 3
    is_decaying = res.decay_ratio < 0.5 and self._peak_decay_ticks > decay_ticks_threshold
    
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


# 2. Patch DisplacementEngine
import axonai.realtime.displacement_engine as disp_mod
original_classify = disp_mod.DisplacementEngine._classify

def patched_classify(self, velocity, disp_ratio, net_move, total_move):
    z = velocity.z_score
    is_high_vel = velocity.is_unusual or z > 1.5
    is_decaying = velocity.is_decaying
    
    if is_decaying and total_move > 3.0:
        return "EXHAUSTION"

    if is_high_vel and disp_ratio >= self._impulse_threshold:
        return "IMPULSE"

    if (is_high_vel or True) and disp_ratio < self._trap_threshold:
        if velocity.tick_efficiency < 0.15:
            return "ABSORPTION"
        return "TRAP"

    is_low_vel = z < self._compression_z
    if is_low_vel and disp_ratio < 0.3 and velocity.tick_efficiency < 0.3:
        return "COMPRESSION"

    return "NEUTRAL"

disp_mod.DisplacementEngine._classify = patched_classify


# 3. Patch LiquidityEngine - only append to active_sweeps if ls.is_currently_breached
import axonai.realtime.liquidity_engine as liq_mod
from axonai.realtime.liquidity_engine import LevelState, LiquidityState

def patched_sync_levels(self, price_levels):
    active_prices = set()
    for pl in price_levels:
        if not pl.is_active:
            continue
            
        active_prices.add(pl.price)
        if pl.price not in self._levels:
            ls = LevelState(
                price=pl.price,
                level_type=pl.level_type,
                strength_score=pl.strength,
                touches=pl.touches
            )
            ls.direction = pl.direction
            self._levels[pl.price] = ls
        else:
            self._levels[pl.price].strength_score = pl.strength
            self._levels[pl.price].level_type = pl.level_type
            self._levels[pl.price].direction = pl.direction
            
    for p in list(self._levels.keys()):
        if p not in active_prices:
            del self._levels[p]

liq_mod.LiquidityEngine.sync_levels = patched_sync_levels

def patched_liq_update(self, price, timestamp, velocity, displacement):
    ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
    dt = ts - self._last_time if self._last_time > 0 else 0.0
    
    if dt > 5.0:
        for ls in self._levels.values():
            ls.is_currently_breached = False
            ls.time_since_breach_sec = 0.0
            
    active_sweeps = []
    active_breaks = []
    
    sorted_levels = sorted(self._levels.values(), key=lambda x: x.price)
    support = None
    resistance = None
    
    for ls in sorted_levels:
        if ls.price < price:
            support = ls
        elif ls.price > price and resistance is None:
            resistance = ls
            
        dist_pips = (price - ls.price) / self._pip
        abs_dist = abs(dist_pips)
        
        was_breached = ls.is_currently_breached
        
        if abs_dist < self._proximity:
            if not was_breached and abs_dist <= 1.0:
                ls.is_currently_breached = True
                ls.touches += 1
        
        if ls.is_currently_breached:
            ls.time_since_breach_sec += dt
            ls.max_breach_depth_pips = max(ls.max_breach_depth_pips, abs_dist)
            self._evaluate_breach(ls, price, velocity, displacement, ts)
            
            if abs_dist > self._proximity and ls.time_since_breach_sec > 10.0:
                ls.is_currently_breached = False
                ls.time_since_breach_sec = 0.0
                
        # ONLY APPEND SWEEP IF CURRENTLY BREACHED (price is close to it)
        if ls.is_swept and ls.is_currently_breached:
            active_sweeps.append(ls)
        if ls.is_broken and ls.is_currently_breached:
            active_breaks.append(ls)

    nearest_dist = float('inf')
    if support:
        nearest_dist = min(nearest_dist, (price - support.price) / self._pip)
    if resistance:
        nearest_dist = min(nearest_dist, (resistance.price - price) / self._pip)
        
    is_void = nearest_dist > 25.0 and velocity.percentile > 80.0

    self._last_price = price
    self._last_time = ts

    return LiquidityState(
        active_sweeps=active_sweeps,
        active_breaks=active_breaks,
        nearest_support=support,
        nearest_resistance=resistance,
        liquidity_void_active=is_void,
        distance_to_nearest_level=round(nearest_dist, 1) if nearest_dist != float('inf') else 0.0
    )

def patched_liq_eval(self, ls, price, vel, disp, ts):
    if ls.is_swept or ls.is_broken:
        if ls.max_breach_depth_pips > 15.0 and not ls.is_currently_breached:
            ls.is_swept = False
            ls.is_broken = False
        return

    is_major_level = ls.level_type in ("PDH", "PDL", "PWH", "PWL", "ASH", "ASL", "H4_SWING")
    
    has_returned = False
    direction = getattr(ls, "direction", "support")
    if is_major_level and ls.max_breach_depth_pips >= 0.5:
        if direction == "support" and price > ls.price + 0.2 * self._pip:
            has_returned = True
        elif direction == "resistance" and price < ls.price - 0.2 * self._pip:
            has_returned = True

    is_broken = False
    if ls.max_breach_depth_pips > 8.0:
        is_broken = True

    if has_returned:
        ls.sweep_probability = 1.0
        ls.is_swept = True
        logger.info(f"!!! MAJOR GEOMETRIC SWEEP DETECTED !!! Level: {ls.price} ({ls.level_type}), dir={direction}, max_depth={ls.max_breach_depth_pips:.2f}")
    elif is_broken:
        ls.acceptance_probability = 1.0
        ls.is_broken = True

liq_mod.LiquidityEngine.update = patched_liq_update
liq_mod.LiquidityEngine._evaluate_breach = patched_liq_eval


# 4. Patch EntryStateMachine._evaluate_idle to use correct reversal direction
import axonai.realtime.entry_state_machine as esm_mod

def patched_evaluate_idle(self, price, ts, vel, disp, liq, regime):
    is_climax = vel.is_unusual and vel.tick_efficiency < 0.2
    is_sweep = len(liq.active_sweeps) > 0
    
    direction = ""
    if is_sweep:
        sweep_lvl = liq.active_sweeps[0]
        lvl_dir = getattr(sweep_lvl, "direction", "")
        if not lvl_dir:
            is_supp = "support" in sweep_lvl.level_type.lower() or any(x in sweep_lvl.level_type for x in ("SUPPORT", "PDL", "PWL", "ASL", "LDL", "TODAY_L"))
            lvl_dir = "support" if is_supp else "resistance"
            
        if lvl_dir == "support":
            direction = "BUY"
        else:
            direction = "SELL"
    elif is_climax:
        if disp.net_displacement_pips > 0:
            direction = "SELL"
        elif disp.net_displacement_pips < 0:
            direction = "BUY"
            
    if (is_climax or is_sweep) and direction:
        self._anomaly_time = ts
        self._anomaly_price = price
        self._anomaly_direction = direction
        self._anomaly_type = "sweep" if is_sweep else "climax"
        self._max_adverse_excursion = 0.0
        
        reason = "Sweep detected" if is_sweep else "Microstructure climax"
        self._transition("ANOMALY", f"{reason}. Expected reversal: {direction}")

esm_mod.EntryStateMachine._evaluate_idle = patched_evaluate_idle


# 5. Patch register_trade
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


# 6. Patch AdaptiveExitManager to only scale sweep target when trend-aligned
import axonai.realtime.adaptive_exit as exit_mod
original_evaluate_exit = exit_mod.AdaptiveExitManager.evaluate

def patched_evaluate_exit(self, current_price, health, regime, liquidity, velocity, displacement, phase, phase_confidence, exit_stats=None, mtf=None, atr=None):
    is_trend_aligned = False
    if mtf is not None:
        if self._direction == "BUY" and mtf.alignment_score > 0.3:
            is_trend_aligned = True
        elif self._direction == "SELL" and mtf.alignment_score < -0.3:
            is_trend_aligned = True

    original_is_sweep = getattr(self, "_is_sweep", False)
    if original_is_sweep and not is_trend_aligned:
        self._is_sweep = False
        
    try:
        res = original_evaluate_exit(self, current_price, health, regime, liquidity, velocity, displacement, phase, phase_confidence, exit_stats, mtf, atr)
    finally:
        self._is_sweep = original_is_sweep
        
    return res

exit_mod.AdaptiveExitManager.evaluate = patched_evaluate_exit


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
logger.info("Starting run with fresh sweep filter...")
report = engine.run()
sweep_trades = sum(1 for t in engine.simulated_trades if "sweep" in t["trigger"])
logger.info(f"Execution complete. Total trades: {report['total_trades']}, Sweep trades: {sweep_trades}, Net P&L: {report['net_profit_pips']:.1f}")

print("\nID | Trigger | Direction | Pips | Close Reason")
print("---|---|---|---|---")
for t in engine.simulated_trades:
    trig = "sweep" if "sweep" in t["trigger"] else "climax"
    print(f"{t['id']} | {trig} | {t['direction']} | {t['pips']:+.1f} | {t['close_reason']}")
