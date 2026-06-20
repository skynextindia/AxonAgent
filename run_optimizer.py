#!/usr/bin/env python3
"""Parameters Optimizer for AxonAI.

Performs a grid search over strategy parameters to maximize Win Rate, Net Profit, and Profit Factor.
"""

from __future__ import annotations

import logging
import sys
import random
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("optimizer")

# Placeholders for child process workers
_worker_candles = []
_worker_ticks = []

def init_worker(candles, ticks):
    global _worker_candles, _worker_ticks
    _worker_candles = candles
    _worker_ticks = ticks

# Patches
import axonai.dataflows.mt5_data as mt5_mod
mt5_mod.mt5_initialize = lambda *a, **kw: True
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._fetch_bars = lambda *a, **kw: _worker_candles
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

import axonai.realtime.backtester as bt_mod
bt_mod.mt5_initialize = lambda *a, **kw: True
bt_mod.get_broker_tz_offset = lambda *a, **kw: 2
bt_mod._fetch_bars = lambda *a, **kw: _worker_candles
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
bt_mod.BacktestEngine.load_historical_data = lambda self: (
    [LiveCandle(timeframe="M15", open_time=c["time"], open=float(c["open"]), high=float(c["high"]), low=float(c["low"]), close=float(c["close"]), volume=int(c["volume"])) for c in _worker_candles],
    _worker_ticks
)

from axonai.realtime.event_types import LiveCandle
from axonai.realtime.backtester import BacktestEngine

def run_simulation(config: dict) -> dict:
    global _worker_candles, _worker_ticks
    engine = BacktestEngine(ticker="EURUSD=X", days=29, config=config)
    report = engine.run()
    # Debug print for control config
    if config.get("cooldown_seconds") == 900 and config.get("loss_cooldown_minutes") == 45:
        print(f"DEBUG WORKER: config={config}")
        print(f"DEBUG WORKER: len(_worker_candles)={len(_worker_candles)}, len(_worker_ticks)={len(_worker_ticks)}")
        print(f"DEBUG WORKER: total_trades={report['total_trades']}, win_rate={report['win_rate_percent']}, net_profit={report['net_profit_pips']}")
        if engine.simulated_trades:
            print("DEBUG WORKER first trade:", engine.simulated_trades[0])
    return report

def main():
    print("Starting parameter space sweep to calibrate system...")
    print("Searching for configs that maximize Win Rate, Net Profit, and Profit Factor.")
    print("=" * 75)

    # 1. Load Data (only in parent process)
    csv_path = "eurusd_m15_may2026.csv"
    try:
        df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    except FileNotFoundError:
        print("CSV data file eurusd_m15_may2026.csv not found. Please run run_intraday_backtest.py first to fetch data.")
        sys.exit(1)

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

    # Populate parent process variables
    init_worker(candle_rows, ticks_list)

    search_space = []
    for min_q in [0.50, 0.55, 0.60]:
        for sl_atr in [1.0, 1.2]:
            for tp_atr in [1.5, 2.0, 2.5]:
                for cool in [120, 300, 600]:
                    for loss_cool in [5, 15, 30]:
                        for decay_factor in [0.25, 0.50, 0.75]:
                            search_space.append({
                                "min_signal_quality": min_q,
                                "sl_atr_multiple": sl_atr,
                                "tp_atr_multiple": tp_atr,
                                "cooldown_seconds": cool,
                                "loss_cooldown_minutes": loss_cool,
                                "realtime_velocity_decay_profit_factor": decay_factor,
                                "backtest_mode": True
                            })

    # Add the current baseline config as control
    search_space.append({
        "min_signal_quality": 0.60,
        "sl_atr_multiple": 1.2,
        "tp_atr_multiple": 2.0,
        "cooldown_seconds": 900,
        "loss_cooldown_minutes": 45,
        "realtime_velocity_decay_profit_factor": 0.25,
        "backtest_mode": True
    })

    results = []
    total = len(search_space)

    
    import os
    max_workers = max(1, (os.cpu_count() or 4) // 2)
    import concurrent.futures
    print(f"Running parameter space sweep in parallel using {max_workers} workers...")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=init_worker,
        initargs=(candle_rows, ticks_list)
    ) as executor:
        futures = {executor.submit(run_simulation, cfg): cfg for cfg in search_space}
        for idx, future in enumerate(concurrent.futures.as_completed(futures), 1):
            cfg = futures[future]
            try:
                report = future.result()
                results.append({
                    "config": cfg,
                    "total_trades": report["total_trades"],
                    "wins": report["wins"],
                    "losses": report["losses"],
                    "win_rate_percent": report["win_rate_percent"],
                    "net_profit_pips": report["net_profit_pips"],
                    "profit_factor": report["profit_factor"]
                })
                print(f"Sweeping parameter space: {idx}/{total} completed...", flush=True)
            except Exception as e:
                print(f"Error sweeping config {cfg}: {e}", flush=True)
                continue

    print("\nSweep complete. Sorting results...")

    # Filter out configurations with 0 trades
    results = [r for r in results if r["total_trades"] > 0]

    # Sort primarily by net profit pips, then win rate
    results.sort(key=lambda x: (x["net_profit_pips"], x["win_rate_percent"]), reverse=True)

    print("\nTOP 10 CONFIGURATIONS BY NET PROFIT:")
    print("-" * 115)
    print(f"{'Rank':<5} | {'Min Qual':<8} | {'SL ATR':<6} | {'TP ATR':<6} | {'Cool (s)':<8} | {'L Cool(m)':<9} | {'Dec Fact':<8} | {'Trades':<6} | {'Win Rate':<8} | {'Net Pips':<8} | {'PF':<5}")
    print("-" * 115)
    for idx, r in enumerate(results[:10], 1):
        c = r["config"]
        dec_f = c.get("realtime_velocity_decay_profit_factor", 0.25)
        print(f"{idx:<5} | {c['min_signal_quality']:<8.2f} | {c['sl_atr_multiple']:<6.1f} | {c['tp_atr_multiple']:<6.1f} | {c['cooldown_seconds']:<8} | {c['loss_cooldown_minutes']:<9} | {dec_f:<8.2f} | {r['total_trades']:<6} | {r['win_rate_percent']:<7.1f}% | {r['net_profit_pips']:<+8.1f} | {r['profit_factor']:.2f}")

    print("-" * 115)
    
    # Sort primarily by win rate (minimizing trades to avoid overfitting)
    results.sort(key=lambda x: (x["win_rate_percent"], x["net_profit_pips"]), reverse=True)

    print("\nTOP 10 CONFIGURATIONS BY WIN RATE:")
    print("-" * 115)
    print(f"{'Rank':<5} | {'Min Qual':<8} | {'SL ATR':<6} | {'TP ATR':<6} | {'Cool (s)':<8} | {'L Cool(m)':<9} | {'Dec Fact':<8} | {'Trades':<6} | {'Win Rate':<8} | {'Net Pips':<8} | {'PF':<5}")
    print("-" * 115)
    for idx, r in enumerate(results[:10], 1):
        c = r["config"]
        dec_f = c.get("realtime_velocity_decay_profit_factor", 0.25)
        print(f"{idx:<5} | {c['min_signal_quality']:<8.2f} | {c['sl_atr_multiple']:<6.1f} | {c['tp_atr_multiple']:<6.1f} | {c['cooldown_seconds']:<8} | {c['loss_cooldown_minutes']:<9} | {dec_f:<8.2f} | {r['total_trades']:<6} | {r['win_rate_percent']:<7.1f}% | {r['net_profit_pips']:<+8.1f} | {r['profit_factor']:.2f}")


    print("-" * 105)


if __name__ == "__main__":
    main()
