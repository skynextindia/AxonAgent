#!/usr/bin/env python3
"""Intraday backtest runner — London/NY sessions only, EOD force-close.

Usage:
    python run_intraday_backtest.py --start 2026-06-01 --end 2026-06-21 --winning-config
"""

import logging
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("intraday_bt")

# ---------------------------------------------------------------------------
# Parse Command-Line Arguments
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Intraday backtest runner")
parser.add_argument("--start", type=str, default="2026-05-01", help="Start date (YYYY-MM-DD)")
parser.add_argument("--end", type=str, default="2026-05-30", help="End date (YYYY-MM-DD)")
parser.add_argument("--ticker", type=str, default="EURUSD=X", help="Ticker (default: EURUSD=X)")
parser.add_argument("--winning-config", action="store_true", help="Force winning configuration calibration parameters")
parser.add_argument("--csv-path", type=str, default=None, help="Explicit CSV cache path")
args = parser.parse_args()

start_date = args.start
end_date = args.end
ticker = args.ticker

# Formulate CSV path dynamically if not explicitly provided
if args.csv_path:
    csv_path = args.csv_path
else:
    clean_ticker = ticker.replace("=X", "").replace("/", "").lower()
    clean_start = start_date.replace("-", "")
    clean_end = end_date.replace("-", "")
    csv_path = f"{clean_ticker}_m15_{clean_start}_{clean_end}.csv"

# ---------------------------------------------------------------------------
# 1. Fetch EURUSD M15 data via yFinance
# ---------------------------------------------------------------------------
import pandas as pd
import yfinance as yf

try:
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    logger.info("Loaded existing CSV with %d rows from %s", len(df), csv_path)
except FileNotFoundError:
    logger.info("Fetching %s M15 data from yFinance (%s to %s)...", ticker, start_date, end_date)
    eur = yf.Ticker(ticker)
    df = eur.history(start=start_date, end=end_date, interval="15m")
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

# Monkey-patch the MT5 module methods FIRST before importing BacktestEngine
import axonai.dataflows.mt5_data as mt5_mod

def patched_init(*args, **kwargs):
    logger.info("MT5 monkey-patch: mt5_initialize() → True (using yFinance data)")
    return True

mt5_mod.mt5_initialize = patched_init
mt5_mod.get_broker_tz_offset = lambda *a, **kw: 2
mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")
mt5_mod._ensure_symbol_visible = lambda sym: None

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

def patched_fetch_bars(symbol: str, timeframe: str, from_date, to_date):
    """Return our pre-built candles instead of calling MT5."""
    logger.info("MT5 monkey-patch: fetch_bars(%s, %s, %s → %s) returning %d bars",
                symbol, timeframe, from_date, to_date, len(candle_rows))
    return candle_rows

mt5_mod._fetch_bars = patched_fetch_bars

# Now import BacktestEngine
from axonai.realtime.backtester import BacktestEngine
import axonai.realtime.backtester as bt_mod

# Also patch the local names in backtester module (imported at module level)
bt_mod.mt5_initialize = patched_init
bt_mod.get_broker_tz_offset = lambda *a, **kw: 2
bt_mod._fetch_bars = patched_fetch_bars
bt_mod._ensure_symbol_visible = lambda sym: None
bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker.replace("=X", "").replace("/", "")

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

    candle_close_time = t + timedelta(minutes=15)
    for i, price in enumerate(tick_prices):
        tick_time = candle_close_time - timedelta(seconds=(n_ticks - 1 - i) * 0.05)
        hs = half_spread + spread_jitter[i]
        bid = round(price - hs, 5)
        ask = round(price + hs, 5)
        ticks_list.append((bid, ask, tick_time))

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

# Formulate config parameters
if args.winning_config:
    logger.info("Using WINNING strategy parameters from calibration.")
    config = {
        "min_signal_quality": 0.50,
        "sl_atr_multiple": 1.0,
        "tp_atr_multiple": 2.0,
        "cooldown_seconds": 900,
        "loss_cooldown_minutes": 45,
        "realtime_velocity_decay_profit_factor": 0.75,
        "stagnation_limit": 2700,
        "drawdown_limit_trending": 2400,
        "drawdown_limit_ranging": 2700,
    }
else:
    logger.info("Using DEFAULT baseline configuration.")
    config = {
        "min_signal_quality": 0.50,
        "sl_atr_multiple": 1.0,
        "tp_atr_multiple": 1.5,
        "cooldown_seconds": 300,
        "loss_cooldown_minutes": 45,
        "realtime_velocity_decay_profit_factor": 0.75,
        "stagnation_limit": 2700,
        "drawdown_limit_trending": 2400,
        "drawdown_limit_ranging": 2700,
    }

# Ticker-specific optimized parameter overrides
clean_t = ticker.upper().replace("=X", "").replace("/", "")
if "GBPUSD" in clean_t:
    logger.info("Applying GBPUSD optimized parameters (tight SL, wide TP multiple).")
    config["sl_atr_multiple"] = 0.8
    config["tp_atr_multiple"] = 4.0
    config["min_signal_quality"] = 0.55
elif "USDJPY" in clean_t:
    logger.info("Applying USDJPY optimized parameters (defensive SL/TP multiples).")
    config["sl_atr_multiple"] = 1.6
    config["tp_atr_multiple"] = 2.0
    config["min_signal_quality"] = 0.60
elif "AUDUSD" in clean_t:
    logger.info("Applying AUDUSD optimized parameters (looser quality floor to capture trades).")
    config["sl_atr_multiple"] = 1.0
    config["tp_atr_multiple"] = 2.0
    config["min_signal_quality"] = 0.45
else:
    logger.info("Applying EURUSD baseline parameters.")
    config["sl_atr_multiple"] = 1.0
    config["tp_atr_multiple"] = 2.0
    config["min_signal_quality"] = 0.50

# Calculate days dynamically
start_dt = datetime.strptime(start_date, "%Y-%m-%d")
end_dt = datetime.strptime(end_date, "%Y-%m-%d")
days = max(1, (end_dt - start_dt).days)

engine = BacktestEngine(
    ticker=ticker,
    days=days,
    config=config
)

logger.info("Starting INTRADAY backtest on real %s M15 data (%d bars, %d days)...", ticker, len(candle_rows), days)
report = engine.run()

# ---------------------------------------------------------------------------
# 4. Print + save results
# ---------------------------------------------------------------------------
out_dir = Path("reports")
out_dir.mkdir(exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
clean_ticker = ticker.replace("=X", "").replace("/", "")
report_suffix = f"{clean_ticker}_{start_date.replace('-', '')}_{end_date.replace('-', '')}_{ts}"
md_path = out_dir / f"intraday_bt_{report_suffix}.md"
json_path = out_dir / f"intraday_bt_{report_suffix}.json"

# Generate markdown report
lines = [
    f"# AxonAI Intraday Backtest — {ticker} ({start_date} to {end_date})",
    f"**Execution Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    f"**Config Mode**: {'Winning Calibration' if args.winning_config else 'Baseline Default'}",
    "**Rules**: London/NY sessions only · EOD force-close · Max 1 trade",
    "",
    "## Configuration Used",
    "| Parameter | Value |",
    "| :--- | :--- |",
    f"| Min Signal Quality | {config['min_signal_quality']} |",
    f"| SL ATR Multiple | {config['sl_atr_multiple']} |",
    f"| TP ATR Multiple | {config['tp_atr_multiple']} |",
    f"| Cooldown Seconds | {config['cooldown_seconds']} |",
    f"| Loss Cooldown Minutes | {config['loss_cooldown_minutes']} |",
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
    "| ID | Direction | Entry Time (UTC) | Entry | Exit Time (UTC) | Exit | Pips | Signal | Exit Reason |",
    "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
]
if engine.simulated_trades:
    for t in engine.simulated_trades:
        status = "✅" if t["status"] == "WIN" else "❌"
        lines.append(
            f"| {t['id']} | {t['direction']} | {t['entry_time'].strftime('%d-%m-%y %H:%M')} UTC "
            f"| {t['entry_price']:.5f} | {t['exit_time'].strftime('%d-%m-%y %H:%M') if t['exit_time'] else '—'} UTC"
            f" | {t['exit_price']:.5f} | {t['pips']:+.1f} {status} | {t['trigger']} | {t.get('close_reason', '—')} |"
        )

with open(md_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

# Clean JSON-friendly report
json_report = {
    "ticker": ticker,
    "start_date": start_date,
    "end_date": end_date,
    "winning_config": args.winning_config,
    "days": days,
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
    import json
    json.dump(json_report, f, indent=2)

logger.info("Report saved → %s", md_path)
logger.info("Summary JSON → %s", json_path)

# Console summary
print()
print("=" * 65)
print(f"  INTRADAY BACKTEST RESULTS — {ticker} ({start_date} to {end_date})")
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
