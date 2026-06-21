#!/usr/bin/env python3
import os
import sys
import logging
# Disable verbose logging to avoid stdout bottlenecks
logging.disable(logging.CRITICAL)

from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import concurrent.futures

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import required modules
import axonai.dataflows.mt5_data as mt5_mod
from axonai.realtime.event_types import LiveCandle
from axonai.realtime.backtester import BacktestEngine
import axonai.realtime.backtester as bt_mod

# Placeholders for worker processes
_worker_may_candles = []
_worker_may_ticks = []
_worker_june_candles = []
_worker_june_ticks = []

def init_worker(may_candles, may_ticks, june_candles, june_ticks):
    global _worker_may_candles, _worker_may_ticks, _worker_june_candles, _worker_june_ticks
    _worker_may_candles = may_candles
    _worker_may_ticks = may_ticks
    _worker_june_candles = june_candles
    _worker_june_ticks = june_ticks

# Patches
mt5_mod.mt5_initialize = lambda *a, **kw: True
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

bt_mod.mt5_initialize = lambda *a, **kw: True
bt_mod.get_broker_tz_offset = lambda *a, **kw: 2
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")

# Global simulation candle/tick pointers
_active_candles = []
_active_ticks = []

def patched_fetch_bars(symbol: str, timeframe: str, from_date, to_date):
    return _active_candles

mt5_mod._fetch_bars = patched_fetch_bars
bt_mod._fetch_bars = patched_fetch_bars

def patched_load_historical_data(self):
    return [
        LiveCandle(
            timeframe="M15",
            open_time=c["time"],
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=int(c["volume"]),
        )
        for c in _active_candles
    ], _active_ticks

bt_mod.BacktestEngine.load_historical_data = patched_load_historical_data

def load_dataset(csv_path: str):
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

    return candle_rows, ticks_list

def run_simulation_job(config: dict) -> dict:
    global _active_candles, _active_ticks, _worker_may_candles, _worker_may_ticks, _worker_june_candles, _worker_june_ticks
    
    # 1. Run May
    _active_candles = _worker_may_candles
    _active_ticks = _worker_may_ticks
    engine_may = BacktestEngine(ticker="EURUSD=X", days=29, config=config)
    report_may = engine_may.run()
    
    # 2. Run June
    _active_candles = _worker_june_candles
    _active_ticks = _worker_june_ticks
    engine_june = BacktestEngine(ticker="EURUSD=X", days=20, config=config)
    report_june = engine_june.run()
    
    return {
        "may_pnl": report_may["net_profit_pips"],
        "may_trades": report_may["total_trades"],
        "may_pf": report_may["profit_factor"],
        "june_pnl": report_june["net_profit_pips"],
        "june_trades": report_june["total_trades"],
        "june_pf": report_june["profit_factor"]
    }

def main():
    print("Loading May and June 2026 data...", flush=True)
    may_candles, may_ticks = load_dataset("eurusd_m15_may2026.csv")
    june_candles, june_ticks = load_dataset("eurusd_m15_20260601_20260621.csv")
    
    print(f"May: {len(may_candles)} candles, {len(may_ticks)} ticks", flush=True)
    print(f"June: {len(june_candles)} candles, {len(june_ticks)} ticks", flush=True)

    # Build targeted search space
    search_space = []
    
    # We sweep 2 strategy modes (winning parameter values vs baseline default values)
    # Mode 1: Baseline Defaults
    # Mode 2: Winning Calibration
    modes = [
        # Baseline Defaults
        {
            "min_signal_quality": 0.60,
            "sl_atr_multiple": 1.0,
            "tp_atr_multiple": 1.5,
            "cooldown_seconds": 300,
            "loss_cooldown_minutes": 30,
            "realtime_velocity_decay_profit_factor": 0.75,
            "label": "Baseline"
        },
        # Winning Calibration
        {
            "min_signal_quality": 0.65,
            "sl_atr_multiple": 1.0,
            "tp_atr_multiple": 2.0,
            "cooldown_seconds": 900,
            "loss_cooldown_minutes": 45,
            "realtime_velocity_decay_profit_factor": 0.75,
            "label": "Winning"
        }
    ]
    
    drawdown_trending_vals = [1200, 1800, 2400]    # 20m, 30m, 40m
    drawdown_ranging_vals = [2700, 3600]           # 45m, 60m
    stagnation_limit_vals = [2700, 3600]           # 45m, 60m
    
    for mode in modes:
        for dd_trend in drawdown_trending_vals:
            for dd_range in drawdown_ranging_vals:
                for stag in stagnation_limit_vals:
                    cfg = mode.copy()
                    cfg.update({
                        "drawdown_limit_trending": dd_trend,
                        "drawdown_limit_ranging": dd_range,
                        "stagnation_limit": stag,
                        "backtest_mode": True
                    })
                    search_space.append(cfg)
                                    
    total = len(search_space)
    print(f"Total search space combinations: {total}", flush=True)
    
    results = []
    max_workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"Running sweep in parallel using {max_workers} worker processes...", flush=True)
    
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=init_worker,
        initargs=(may_candles, may_ticks, june_candles, june_ticks)
    ) as executor:
        futures = {executor.submit(run_simulation_job, cfg): cfg for cfg in search_space}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
            cfg = futures[future]
            try:
                rep = future.result()
                results.append({
                    "config": cfg,
                    "may_pnl": rep["may_pnl"],
                    "may_trades": rep["may_trades"],
                    "may_pf": rep["may_pf"],
                    "june_pnl": rep["june_pnl"],
                    "june_trades": rep["june_trades"],
                    "june_pf": rep["june_pf"],
                    "combined_pnl": rep["may_pnl"] + rep["june_pnl"]
                })
                print(f"Sweep progress: {idx}/{total} completed...", flush=True)
            except Exception as e:
                print(f"Error evaluating config: {e}", flush=True)
                
    # Sort by combined P&L
    results.sort(key=lambda x: x["combined_pnl"], reverse=True)
    
    print("\nTOP CONFIGURATIONS SORTED BY COMBINED NET P&L:", flush=True)
    print("-" * 150, flush=True)
    print(f"{'Rank':<4} | {'Mode':<8} | {'dd-t':<4} | {'dd-r':<4} | {'stag':<4} | {'May PnL':<8} | {'May Tr':<6} | {'May PF':<6} | {'Jun PnL':<8} | {'Jun Tr':<6} | {'Jun PF':<6} | {'Comb PnL':<8}", flush=True)
    print("-" * 150, flush=True)
    for idx, r in enumerate(results[:20], 1):
        c = r["config"]
        print(f"{idx:<4} | {c['label']:<8} | {c['drawdown_limit_trending']:<4} | {c['drawdown_limit_ranging']:<4} | {c['stagnation_limit']:<4} | {r['may_pnl']:<+8.1f} | {r['may_trades']:<6} | {r['may_pf']:<6.2f} | {r['june_pnl']:<+8.1f} | {r['june_trades']:<6} | {r['june_pf']:<6.2f} | {r['combined_pnl']:<+8.1f}", flush=True)
    print("-" * 150, flush=True)

if __name__ == "__main__":
    main()
