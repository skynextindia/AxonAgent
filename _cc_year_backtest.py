"""1-year REAL MT5 backtest — EURUSD intraday, $10,000 account.

Pulls real M15 bars from the connected MT5 terminal (no synthetic fallback),
runs the intraday peak-reversal strategy, and models $ P&L on a $10k account
with 1%-risk-per-trade compounding sizing. Scratch script (safe to delete).
"""
import sys
import csv
import logging
from datetime import datetime

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from axonai.realtime.backtester import BacktestEngine
from axonai.dataflows.mt5_data import mt5_initialize
from axonai.default_config import DEFAULT_CONFIG

DAYS = 365
START_EQUITY = 10_000.0
RISK_PCT = 0.01            # 1% of equity risked per trade
PIP_VALUE_PER_LOT = 10.0  # USD per pip per 1.0 lot, EURUSD
PIP = 0.0001

if not mt5_initialize():
    print("MT5 INIT FAILED — cannot run a REAL-data backtest."); sys.exit(1)

import MetaTrader5 as mt5
acc = mt5.account_info()
print(f"MT5 connected: {acc.server} ({'DEMO' if acc.trade_mode==0 else 'REAL' if acc.trade_mode==2 else 'CONTEST'})")

config = DEFAULT_CONFIG.copy()
config["realtime_dry_run"] = True
config["backtest_progress_every"] = 200  # emit a PROGRESS line every 200 M15 bars

engine = BacktestEngine(ticker="EURUSD=X", days=DAYS, config=config)
print(f"Running REAL 1-year backtest (EURUSD M15, {DAYS} days)... this may take a few minutes.")
report = engine.run()

trades = [t for t in engine.simulated_trades if t.get("exit_price") is not None]

print("\n" + "=" * 60)
print("  REAL-DATA RESULT — 1Y EURUSD M15 (intraday)")
print("=" * 60)
print(f"  Bars source:   MT5 real M15 (ticks interpolated 15/bar)")
print(f"  Total trades:  {report['total_trades']}")
print(f"  Wins/Losses:   {report['wins']} / {report['losses']}")
print(f"  Win rate:      {report['win_rate_percent']:.1f}%")
print(f"  Net pips:      {report['net_profit_pips']:+.1f}")
print(f"  Profit factor: {report['profit_factor']:.2f}")

# --- $ P&L on a $10k account, 1% risk/trade, compounding ---
equity = START_EQUITY
peak = equity
max_dd = 0.0
wins_usd = 0.0
loss_usd = 0.0
MIN_LOT = 0.01
MAX_LOT = 100.0   # hard broker cap; also prevents float overflow

for t in trades:
    sl_pips = abs(t["entry_price"] - t["sl"]) / PIP
    if sl_pips <= 0 or equity <= 0:
        continue
    risk_amt = equity * RISK_PCT
    lot = risk_amt / (sl_pips * PIP_VALUE_PER_LOT)
    lot = max(MIN_LOT, min(lot, MAX_LOT))   # clamp to [0.01, 100.0]
    pnl = t["pips"] * lot * PIP_VALUE_PER_LOT
    equity += pnl
    peak = max(peak, equity)
    max_dd = max(max_dd, (peak - equity) / peak)
    if pnl >= 0:
        wins_usd += pnl
    else:
        loss_usd += -pnl

print("\n" + "=" * 60)
print(f"  $ ON ${START_EQUITY:,.0f} ACCOUNT — 1% risk/trade, compounding")
print("=" * 60)
print(f"  Final equity:  ${equity:,.2f}")
print(f"  Net P&L:       ${equity - START_EQUITY:+,.2f}  ({(equity/START_EQUITY - 1)*100:+.1f}%)")
print(f"  Max drawdown:  {max_dd*100:.1f}%")
flat_pnl = report["net_profit_pips"] * 0.10 * PIP_VALUE_PER_LOT
print(f"  (Reference, flat 0.10 lot: ${flat_pnl:+,.2f})")
print("=" * 60)
print("  CAVEAT: spread/slippage/commission NOT modeled; sub-bar ticks are")
print("  interpolated from real M15 OHLC (synthetic microstructure).")
print("=" * 60)

# --- Export all trades to CSV ---
CSV_PATH = "_cc_trades.csv"
fieldnames = ["id", "entry_time", "exit_time", "direction", "signal_type",
              "entry_price", "exit_price", "sl", "tp", "pips", "status", "exit_reason"]
with open(CSV_PATH, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(trades)
print(f"\n  Trades saved to: {CSV_PATH}  ({len(trades)} rows)")

# --- Print trade summary table (first 30 + last 10) ---
print("\n" + "=" * 95)
print(f"  {'#':>4}  {'Entry Time':<20} {'Dir':<5} {'Signal':<28} {'Pips':>7}  {'Status':<6} {'Exit'}")
print("=" * 95)
show = trades[:30] + ([None] if len(trades) > 40 else []) + trades[-10:]
for row in show:
    if row is None:
        print(f"  {'...':>4}  {'  ... ' + str(len(trades)-40) + ' more trades ...'}")
        continue
    et = str(row.get('entry_time', ''))[:19]
    sig = str(row.get('signal_type', row.get('event_type', '')))[:27]
    d = str(row.get('direction', ''))[:4]
    pips = row.get('pips', 0)
    status = row.get('status', '')
    reason = str(row.get('exit_reason', ''))[:12]
    pip_str = f"{pips:+.1f}"
    print(f"  {row['id']:>4}  {et:<20} {d:<5} {sig:<28} {pip_str:>7}  {status:<6} {reason}")
print("=" * 95)

mt5.shutdown()
