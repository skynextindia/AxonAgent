#!/usr/bin/env python3
"""Intraday backtest runner — London/NY sessions only, EOD force-close.

Usage:
    python run_intraday_backtest.py
"""

import logging
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("intraday_bt")

# ---------------------------------------------------------------------------
# 1. Fetch EURUSD M15 data via yFinance
# ---------------------------------------------------------------------------
import pandas as pd
import yfinance as yf

csv_path = "eurusd_m15_may2026.csv"

try:
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    logger.info("Loaded existing CSV with %d rows", len(df))
except FileNotFoundError:
    logger.info("Fetching EURUSD=X M15 data from yFinance (May 1–29, 2026)...")
    eur = yf.Ticker("EURUSD=X")
    df = eur.history(start="2026-05-01", end="2026-05-30", interval="15m")
    if df.empty:
        logger.error("yFinance returned empty DataFrame. Check ticker or date range.")
        sys.exit(1)
    df.to_csv(csv_path)
    logger.info("Saved %d bars to %s", len(df), csv_path)

# Convert to UTC first, then strip tz so all times are UTC-naive
if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)
df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
logger.info("Loaded %d M15 bars from %s to %s", len(df), df.index[0], df.index[-1])

# ---------------------------------------------------------------------------
# 2. Monkey-patch MT5 so the backtester loads our DataFrame instead
# ---------------------------------------------------------------------------
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from axonai.realtime.backtester import BacktestEngine

# Convert DataFrame candles to the format BacktestEngine expects
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

# Build ticks: path-based interpolation (Open→Low→High→Close) matching
# the backtester's own tick generation so trade prices align with chart.
rng = np.random.default_rng(42)
ticks_list = []
for c in candle_rows:
    o, h, l, c_price = c["open"], c["high"], c["low"], c["close"]
    t = c["time"]
    n_ticks = 15  # match backtester's tick count per candle
    half_spread = 0.00005  # 0.5 pip

    if c_price >= o:
        # Bullish: Open → Low → High → Close
        seg1 = np.linspace(o, l, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(l, h, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(h, c_price, n_ticks - len(seg1) - len(seg2))
    else:
        # Bearish: Open → High → Low → Close
        seg1 = np.linspace(o, h, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(h, l, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(l, c_price, n_ticks - len(seg1) - len(seg2))

    tick_prices = np.concatenate([seg1, seg2, seg3])
    # Ensure exactly n_ticks
    tick_prices = tick_prices[:n_ticks]

    # Spread jitter (±0.1 pip around the 0.5-pip half-spread)
    spread_jitter = rng.uniform(-0.00001, 0.00001, n_ticks)

    for i, price in enumerate(tick_prices):
        tick_time = t + timedelta(
            seconds=int((i + 1) * 900 / n_ticks)  # evenly spaced within 15-min candle
        )
        hs = half_spread + spread_jitter[i]
        bid = round(price - hs, 5)
        ask = round(price + hs, 5)
        ticks_list.append((bid, ask, tick_time))

# Monkey-patch the MT5 module methods that BacktestEngine.load_data() calls
import axonai.dataflows.mt5_data as mt5_mod

_real_fetch_bars = mt5_mod._fetch_bars  # keep for reference

def patched_fetch_bars(symbol: str, timeframe: str, from_date, to_date):
    """Return our pre-built candles instead of calling MT5."""
    logger.info("MT5 monkey-patch: fetch_bars(%s, %s, %s → %s) returning %d bars",
                symbol, timeframe, from_date, to_date, len(candle_rows))
    return candle_rows

def patched_init(*args, **kwargs):
    logger.info("MT5 monkey-patch: mt5_initialize() → True (using yFinance data)")
    return True

import axonai.realtime.backtester as bt_mod
mt5_mod.mt5_initialize = patched_init
mt5_mod._fetch_bars = patched_fetch_bars
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

# Also patch the local names in backtester module (imported at module level)
bt_mod.mt5_initialize = patched_init
bt_mod._fetch_bars = patched_fetch_bars
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")

# ---------------------------------------------------------------------------
# 3. Run intraday backtest - patch load_historical_data to use our data
# ---------------------------------------------------------------------------

def patched_load_historical_data(self):
    """Return pre-built candles and ticks, bypassing MT5 entirely."""
    from axonai.realtime.event_types import LiveCandle
    from datetime import datetime

    logger.info("Using pre-built candles: %d bars, %d ticks", len(candle_rows), len(ticks_list))
    candles = []
    for c in candle_rows:
        t = c["time"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t)
        candle = LiveCandle(
            timeframe="M15",
            open_time=t,
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=int(c["volume"]),
        )
        candles.append(candle)
    return candles, ticks_list

bt_mod.BacktestEngine.load_historical_data = patched_load_historical_data

engine = BacktestEngine(
    ticker="EURUSD=X",
    days=29,
)

logger.info("Starting INTRADAY backtest on real EURUSD M15 data (%d bars, 29 days)...", len(candle_rows))
report = engine.run()

# ---------------------------------------------------------------------------
# 4. Print + save results
# ---------------------------------------------------------------------------
out_dir = Path("reports")
out_dir.mkdir(exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
md_path = out_dir / f"intraday_bt_EURUSD_{ts}.md"
json_path = out_dir / f"intraday_bt_EURUSD_{ts}.json"

# Generate markdown report
lines = [
    "# AxonAI Intraday Backtest — EURUSD May 2026",
    f"**Execution Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    "**Rules**: London/NY sessions only · EOD force-close · Max 1 trade",
    "",
    "## Performance Summary",
    f"| Metric | Value |",
    f"| :--- | :--- |",
    f"| **Total Trades** | {report['total_trades']} |",
    f"| **Wins** | {report['wins']} ✅ |",
    f"| **Losses** | {report['losses']} ❌ |",
    f"| **Win Rate** | **{report['win_rate_percent']:.1f}%** |",
    f"| **Net P&L** | **{report['net_profit_pips']:+.1f} pips** |",
    f"| **Profit Factor** | {report['profit_factor']:.2f} |",
    "",
    "## Events Detected",
    "| Event Type | Count |",
    "| :--- | :--- |",
]
for ev_type, count in report.get("event_breakdown", {}).items():
    lines.append(f"| `{ev_type}` | {count} |")

lines += [
    "",
    "## Trade Log",
    "| ID | Direction | Entry Time (UTC) | Entry | Exit Time (UTC) | Exit | Pips | Signal |",
    "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
]
if engine.simulated_trades:
    for t in engine.simulated_trades:
        status = "✅" if t["status"] == "WIN" else "❌"
        lines.append(
            f"| {t['id']} | {t['direction']} | {t['entry_time'].strftime('%d-%m-%y %H:%M')} UTC "
            f"| {t['entry_price']:.5f} | {t['exit_time'].strftime('%d-%m-%y %H:%M') if t['exit_time'] else '—'} UTC"
            f" | {t['exit_price']:.5f} | {t['pips']:+.1f} {status} | {t['trigger']} |"
        )

with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

import json

# Clean JSON-friendly report
json_report = {
    "ticker": "EURUSD=X",
    "days": 29,
    "mode": "intraday",
    "total_trades": report["total_trades"],
    "wins": report["wins"],
    "losses": report["losses"],
    "win_rate_percent": report["win_rate_percent"],
    "net_profit_pips": report["net_profit_pips"],
    "profit_factor": report["profit_factor"],
    "event_breakdown": report.get("event_breakdown", {}),
}
with open(json_path, "w") as f:
    json.dump(json_report, f, indent=2)

logger.info("Report saved → %s", md_path)
logger.info("Summary JSON → %s", json_path)

# Console summary
print()
print("=" * 65)
print("  INTRADAY BACKTEST RESULTS — EURUSD May 2026")
print("  Sessions: London / Overlap / NY only · EOD force-close")
print("=" * 65)
print(f"  Total Trades:    {report['total_trades']}")
print(f"  Wins / Losses:   {report['wins']} / {report['losses']}")
print(f"  Win Rate:        {report['win_rate_percent']:.1f}%")
print(f"  Net P&L:         {report['net_profit_pips']:+.1f} pips")
print(f"  Profit Factor:   {report['profit_factor']:.2f}")
if engine.simulated_trades:
    overnight = sum(1 for t in engine.simulated_trades if t["close_reason"] == "End of Day (Session Close)")
    print(f"  EOD force-closed: {overnight}")
print("=" * 65)
