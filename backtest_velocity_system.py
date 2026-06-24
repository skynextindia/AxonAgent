#!/usr/bin/env python3
"""Velocity Intelligence System Backtest

Tests the new velocity-based entry/exit system against recent EURUSD data.
Compares metrics: Entry quality, Exit timing, Win rate, Profit factor.

Usage:
    python backtest_velocity_system.py --start 2026-05-01 --end 2026-06-21
"""

import logging
import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("velocity_backtest")

# Parse args
parser = argparse.ArgumentParser(description="Velocity Intelligence System Backtest")
parser.add_argument("--start", type=str, default="2026-05-01", help="Start date (YYYY-MM-DD)")
parser.add_argument("--end", type=str, default="2026-06-21", help="End date (YYYY-MM-DD)")
parser.add_argument("--ticker", type=str, default="EURUSD=X", help="Ticker")
parser.add_argument("--csv-path", type=str, default=None, help="CSV cache path")
args = parser.parse_args()

# ───────────────────────────────────────────────────────────────────────────
# 1. FETCH DATA
# ───────────────────────────────────────────────────────────────────────────
import pandas as pd
import yfinance as yf

clean_ticker = args.ticker.replace("=X", "").lower()
clean_start = args.start.replace("-", "")
clean_end = args.end.replace("-", "")
csv_path = args.csv_path or f"{clean_ticker}_m15_{clean_start}_{clean_end}.csv"

try:
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    logger.info("✅ Loaded existing CSV: %d rows from %s", len(df), csv_path)
except FileNotFoundError:
    logger.info("📊 Fetching %s M15 data (%s to %s)...", args.ticker, args.start, args.end)
    eur = yf.Ticker(args.ticker)
    df = eur.history(start=args.start, end=args.end, interval="15m")
    if df.empty:
        logger.error("❌ yFinance returned empty data")
        sys.exit(1)
    df.to_csv(csv_path)
    logger.info("💾 Saved %d bars to %s", len(df), csv_path)

if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)
df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
logger.info("📈 Data loaded: %d M15 candles (%s to %s)", len(df), df.index[0], df.index[-1])

# ───────────────────────────────────────────────────────────────────────────
# 2. MONKEY-PATCH MT5 & INITIALIZE BACKTEST ENGINE
# ───────────────────────────────────────────────────────────────────────────
import numpy as np
import axonai.dataflows.mt5_data as mt5_mod

mt5_mod.mt5_initialize = lambda *a, **kw: True
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

# Convert to candle format
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

logger.info("🔧 MT5 monkey-patched, %d candles ready", len(candle_rows))

# ───────────────────────────────────────────────────────────────────────────
# 3. CREATE CONFIG WITH VELOCITY INTELLIGENCE SETTINGS
# ───────────────────────────────────────────────────────────────────────────
from axonai.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config.update({
    "backtest_mode": True,
    "realtime_magic_number": 123456,
    "realtime_default_lot_size": 0.01,
    "realtime_cooldown_seconds": 300,
    "paper_trade": True,

    # VELOCITY INTELLIGENCE SETTINGS (NEW)
    "realtime_entry_zscore_threshold": 2.0,              # Entry qualification
    "realtime_velocity_health_threshold_exit": 0.40,     # Close if health drops
    "realtime_velocity_health_threshold_trail": 0.70,    # Tighten trail
    "realtime_reversal_risk_threshold": 0.70,            # Close on reversal
    "realtime_velocity_window_size": 30,
    "realtime_pre_entry_baseline_window": 100,
    "realtime_velocity_min_profit_tight_trail": 0.25,
})

logger.info("⚙️  Config loaded with Velocity Intelligence settings")

# ───────────────────────────────────────────────────────────────────────────
# 4. RUN BACKTEST ENGINE
# ───────────────────────────────────────────────────────────────────────────
from axonai.realtime.backtester import BacktestEngine

logger.info("🚀 Initializing backtest engine...")
engine = BacktestEngine(
    ticker=args.ticker,
    days=20,
    config=config,
)

logger.info("▶️  Running backtest with Velocity Intelligence system...")
results = engine.run()

# ───────────────────────────────────────────────────────────────────────────
# 5. EXTRACT & DISPLAY METRICS
# ───────────────────────────────────────────────────────────────────────────
logger.info("\n" + "="*70)
logger.info("BACKTEST RESULTS - VELOCITY INTELLIGENCE SYSTEM")
logger.info("="*70)

trades = results.get("trades", [])
entry_rejections = results.get("entry_rejections", 0)
total_entries_attempted = results.get("total_entries_attempted", 0)

# Basic stats
total_trades = len(trades)
wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
losses = total_trades - wins
win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
total_pnl = sum(t.get("pnl", 0) for t in trades)
avg_win = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0) / wins if wins > 0 else 0
avg_loss = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) <= 0) / losses if losses > 0 else 0
profit_factor = avg_win / abs(avg_loss) if avg_loss != 0 else (float('inf') if avg_win > 0 else 0)

entry_rejection_rate = (entry_rejections / total_entries_attempted * 100) if total_entries_attempted > 0 else 0

logger.info("📊 TRADE STATISTICS")
logger.info(f"  Total Trades:           {total_trades}")
logger.info(f"  Wins / Losses:          {wins} / {losses}")
logger.info(f"  Win Rate:               {win_rate:.1f}%")
logger.info(f"  Avg Win / Loss:         +{avg_win:.2f} / {avg_loss:.2f} pips")
logger.info(f"  Profit Factor:          {profit_factor:.2f}x")
logger.info(f"  Total P&L:              {total_pnl:+.1f} pips")

logger.info("\n🎯 VELOCITY INTELLIGENCE METRICS")
logger.info(f"  Entry Attempts:         {total_entries_attempted}")
logger.info(f"  Entries Rejected:       {entry_rejections} ({entry_rejection_rate:.1f}%)")
logger.info(f"  Entries Accepted:       {total_trades}")

# Analyze exit reasons
exit_reasons = {}
for t in trades:
    reason = t.get("exit_reason", "UNKNOWN")
    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

if exit_reasons:
    logger.info("\n🚪 EXIT REASONS DISTRIBUTION")
    for reason, count in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
        pct = count / total_trades * 100 if total_trades > 0 else 0
        logger.info(f"  {reason:30} {count:3} trades ({pct:5.1f}%)")

# Analyze entry quality (velocity z-score at entry)
entry_zscores = [t.get("entry_zscore", 0) for t in trades]
if entry_zscores:
    avg_zscore = sum(entry_zscores) / len(entry_zscores)
    logger.info(f"\n⚡ ENTRY QUALITY")
    logger.info(f"  Avg Entry Z-Score:     {avg_zscore:.2f}σ (threshold: 2.0σ)")

# Analyze trade duration
durations = [t.get("duration_minutes", 0) for t in trades]
if durations:
    avg_duration = sum(durations) / len(durations)
    logger.info(f"\n⏱️  TRADE DURATION")
    logger.info(f"  Average Duration:      {avg_duration:.0f} minutes")

logger.info("\n" + "="*70)
logger.info("✅ BACKTEST COMPLETE")
logger.info("="*70)

# Save results
results_file = f"backtest_results_velocity_{args.start.replace('-','')}_{args.end.replace('-','')}.json"
import json
with open(results_file, 'w') as f:
    json.dump({
        "date_range": f"{args.start} to {args.end}",
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "total_pnl_pips": total_pnl,
        "profit_factor": profit_factor,
        "entry_rejection_rate_pct": entry_rejection_rate,
        "avg_entry_zscore": avg_zscore if entry_zscores else 0,
        "exit_reasons": exit_reasons,
    }, f, indent=2)

logger.info(f"📁 Results saved to {results_file}")
