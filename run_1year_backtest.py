#!/usr/bin/env python3
"""1-Year Intraday backtest runner fetching real data directly from local MT5.

Usage:
    python run_1year_backtest.py
"""

import logging
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import MetaTrader5 as mt5

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("1year_bt")

# 1. Fetch EURUSD M15 data from MetaTrader 5
symbol = "EURUSD"
logger.info("Initializing MT5 to fetch 1 year of %s M15 historical data...", symbol)
if not mt5.initialize():
    logger.error("MT5 initialization failed! Make sure MT5 terminal is open.")
    sys.exit(1)

# Ensure symbol is visible
mt5.symbol_select(symbol, True)

# Fetch 365 days of M15 bars
utc_to = datetime.now()
utc_from = utc_to - timedelta(days=365)
rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, utc_from, utc_to)
mt5.shutdown()

if rates is None or len(rates) == 0:
    logger.error("Failed to fetch M15 rates from MT5. Rates is empty.")
    sys.exit(1)

logger.info("Fetched %d M15 bars from MT5", len(rates))

# Format MT5 rates into candle_rows
candle_rows = []
for r in rates:
    t = datetime.fromtimestamp(r["time"])
    candle_rows.append({
        "time": t,
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": int(r["tick_volume"]),
    })

# 2. Build ticks: path-based interpolation matching run_intraday_backtest.py
rng = np.random.default_rng(42)
ticks_list = []
logger.info("Interpolating %d ticks from %d candles...", len(candle_rows) * 15, len(candle_rows))

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

    for i, price in enumerate(tick_prices):
        tick_time = t + timedelta(seconds=int((i + 1) * 900 / n_ticks))
        hs = half_spread + spread_jitter[i]
        bid = round(price - hs, 5)
        ask = round(price + hs, 5)
        ticks_list.append((bid, ask, tick_time))

# 3. Setup BacktestEngine with Patches
from axonai.realtime.backtester import BacktestEngine
import axonai.realtime.backtester as bt_mod
import axonai.dataflows.mt5_data as mt5_mod

def patched_fetch_bars(sym: str, timeframe: str, from_date, to_date):
    logger.info("MT5 patch: fetch_bars returning %d candles", len(candle_rows))
    return candle_rows

def patched_init(*args, **kwargs):
    return True

mt5_mod.mt5_initialize = patched_init
mt5_mod._fetch_bars = patched_fetch_bars
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker
mt5_mod._ensure_symbol_visible = lambda sym: None

bt_mod.mt5_initialize = patched_init
bt_mod._fetch_bars = patched_fetch_bars
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker

def patched_load_historical_data(self):
    from axonai.realtime.event_types import LiveCandle
    candles = []
    for c in candle_rows:
        t = c["time"]
        candle = LiveCandle(
            timeframe="M15",
            open_time=t,
            open=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c["volume"],
        )
        candles.append(candle)
    return candles, ticks_list

bt_mod.BacktestEngine.load_historical_data = patched_load_historical_data

engine = BacktestEngine(
    ticker=symbol,
    days=365,
    config={"require_sr_proximity": True, "require_structural_alignment": True},
)

logger.info("Starting 1-YEAR backtest on EURUSD M15 data (%d bars)...", len(candle_rows))
report = engine.run()

# 4. Save report
out_dir = Path("reports")
out_dir.mkdir(exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
md_path = out_dir / f"1year_bt_EURUSD_{ts}.md"
pip_val_usd = 10.0  # $10 per pip for EURUSD 1.00 lot
net_profit_usd = report['net_profit_pips'] * pip_val_usd

lines = [
    "# AxonAI 1-Year Intraday Backtest — EURUSD",
    f"**Execution Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    "**Rules**: London/NY sessions only · EOD force-close · Max 1 trade · Volume: 1.00 Lot",
    "",
    "## Performance Summary",
    "| Metric | Value |",
    "| :--- | :--- |",
    f"| **Total Trades** | {report['total_trades']} |",
    f"| **Wins** | {report['wins']} ✅ |",
    f"| **Losses** | {report['losses']} ❌ |",
    f"| **Win Rate** | **{report['win_rate_percent']:.1f}%** |",
    f"| **Net P&L (Pips)** | **{report['net_profit_pips']:+.1f} pips** |",
    f"| **Net Profit/Loss (USD)** | **${net_profit_usd:+.2f}** |",
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
    "| ID | Direction | Entry Time (UTC) | Entry | Exit Time (UTC) | Exit | Pips | Profit/Loss (USD) | Signal |",
    "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
]
if engine.simulated_trades:
    for t in engine.simulated_trades:
        status = "✅" if t["status"] == "WIN" else "❌"
        trade_usd = t['pips'] * pip_val_usd
        lines.append(
            f"| {t['id']} | {t['direction']} | {t['entry_time'].strftime('%d-%m-%y %H:%M')} UTC "
            f"| {t['entry_price']:.5f} | {t['exit_time'].strftime('%d-%m-%y %H:%M') if t['exit_time'] else '—'} UTC"
            f" | {t['exit_price']:.5f} | {t['pips']:+.1f} {status} | ${trade_usd:+.2f} | {t['trigger']} |"
        )

with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

logger.info("Report saved → %s", md_path)

print()
print("=" * 65)
print("  1-YEAR INTRADAY BACKTEST RESULTS (1.00 LOT)")
print("=" * 65)
print(f"  Total Trades:    {report['total_trades']}")
print(f"  Wins / Losses:   {report['wins']} / {report['losses']}")
print(f"  Win Rate:        {report['win_rate_percent']:.1f}%")
print(f"  Net P&L (Pips):  {report['net_profit_pips']:+.1f} pips")
print(f"  Net P&L (USD):   ${net_profit_usd:+.2f}")
print(f"  Profit Factor:   {report['profit_factor']:.2f}")
if engine.simulated_trades:
    overnight = sum(1 for t in engine.simulated_trades if t["close_reason"] == "End of Day (Session Close)")
    print(f"  EOD force-closed: {overnight}")
print("=" * 65)
