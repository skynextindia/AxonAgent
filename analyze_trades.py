#!/usr/bin/env python3
"""Per-trade MAE / MFE / drawdown analyzer for system tuning.

Pulls CLOSED positions straight from the MT5 terminal's own deal history
(authoritative: server time, real commission + swap), then reconstructs each
trade's price path from M1 bars to measure:

  * MAE  - Maximum Adverse Excursion   : the worst the trade went AGAINST you
                                          while open (the "heat" / true drawdown)
  * MFE  - Maximum Favorable Excursion  : the best it went FOR you while open
                                          (the most you could have banked)
  * capture % = realized / MFE          : how much of the good move you kept
  * give-back = MFE - realized          : favorable pips left on the table

It prints a per-trade table + tuning-oriented summary, and writes a CSV you can
open in Excel.

READ-ONLY. This tool never sends, modifies, or closes an order. It only calls
history_deals_get() and copy_rates_range().

Usage (run inside your venv, MT5 terminal running/logged in):
    python analyze_trades.py --days 30
    python analyze_trades.py --from 2026-07-17 --to 2026-07-23
    python analyze_trades.py --days 60 --symbol EURUSD --csv reports/mae_mfe.csv

Notes:
  * MAE/MFE are measured on M1 bar highs/lows, so they are accurate to ~1 pip,
    not tick-perfect. That is the standard resolution for this kind of analysis.
  * Broker-timezone differences are handled by fetching a padded bar window and
    filtering by each bar's POSIX timestamp, so the window is always correct.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pip_size(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def money_per_pip(symbol: str, volume: float, exit_price: float,
                  gross_profit: float, realized_pips: float) -> float:
    """Account money per pip for this position.

    Preferred: derive it from the trade's own realized gross P/L and pips
    (exact for any symbol/lot, auto-correct for JPY). Fall back to the standard
    contract formula when realized pips are ~0.
    """
    if abs(realized_pips) >= 0.1 and gross_profit != 0.0:
        return abs(gross_profit) / abs(realized_pips)
    # Fallback: canonicalize first so a broker suffix (EURUSD.i) doesn't defeat
    # the quote-currency check.
    letters = "".join(c for c in (symbol or "").upper() if c.isalpha())
    canon = letters[:6]
    pip = pip_size(symbol)
    contract = 100000.0
    if canon.endswith("USD"):                      # XXXUSD: $10/pip/lot
        return 10.0 * volume
    # USD-base or cross: value = contract * pip / price
    return (contract * pip / (exit_price or 1.0)) * volume


def connect(config: dict):
    from axonai.dataflows.mt5_data import mt5_initialize
    ok = mt5_initialize(
        terminal_path=config.get("mt5_terminal_path"),
        login=config.get("mt5_login"),
        password=config.get("mt5_password"),
        server=config.get("mt5_server"),
    )
    if not ok:
        print("ERROR: could not connect to MT5. Make sure the terminal is running "
              "and logged in (the same one the daemon uses).")
        sys.exit(1)


def reconstruct_positions(mt5, dfrom: datetime, dto: datetime):
    """Group raw deals into closed positions in the [dfrom, dto] window."""
    # Pad the fetch by a day each side; the true filter is by close epoch below.
    deals = mt5.history_deals_get(dfrom - timedelta(days=1), dto + timedelta(days=1))
    if deals is None:
        print("ERROR: history_deals_get returned None:", mt5.last_error())
        sys.exit(1)

    by_pos: dict = {}
    for d in deals:
        by_pos.setdefault(d.position_id, []).append(d)

    positions = []
    for pos_id, ds in by_pos.items():
        ds = sorted(ds, key=lambda x: x.time)
        ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry == mt5.DEAL_ENTRY_OUT]
        if not ins or not outs:
            continue  # still open or not a normal round-trip
        ein, eout = ins[0], outs[-1]
        entry_epoch, exit_epoch = ein.time, eout.time
        # Filter by close time inside the requested window
        if not (dfrom.timestamp() <= exit_epoch <= dto.timestamp()):
            continue
        direction = "BUY" if ein.type == mt5.DEAL_TYPE_BUY else "SELL"
        positions.append({
            "pos_id": pos_id,
            "symbol": ein.symbol,
            "direction": direction,
            "volume": ein.volume,
            "entry_price": ein.price,
            "exit_price": eout.price,
            "entry_epoch": entry_epoch,
            "exit_epoch": exit_epoch,
            "gross": sum(d.profit for d in outs),
            "commission": sum(d.commission for d in ds),
            "swap": sum(d.swap for d in ds),
        })
    positions.sort(key=lambda p: p["exit_epoch"])
    return positions


def mae_mfe(mt5, pos: dict, pad_hours: float, buffer_s: int):
    """Return (mae_pips, mfe_pips, n_bars) for a position from M1 bar extremes."""
    sym = pos["symbol"]
    pip = pip_size(sym)
    e, x = pos["entry_epoch"], pos["exit_epoch"]
    lo = datetime.utcfromtimestamp(max(0, e - buffer_s - int(pad_hours * 3600)))
    hi = datetime.utcfromtimestamp(x + buffer_s + int(pad_hours * 3600))
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, lo, hi)
    highs = [pos["entry_price"], pos["exit_price"]]
    lows = [pos["entry_price"], pos["exit_price"]]
    n = 0
    if rates is not None:
        for r in rates:
            if e - buffer_s <= r["time"] <= x + buffer_s:
                highs.append(float(r["high"]))
                lows.append(float(r["low"]))
                n += 1
    max_high, min_low = max(highs), min(lows)
    ep = pos["entry_price"]
    if pos["direction"] == "BUY":
        mae = (ep - min_low) / pip
        mfe = (max_high - ep) / pip
    else:
        mae = (max_high - ep) / pip
        mfe = (ep - min_low) / pip
    return max(0.0, mae), max(0.0, mfe), n


def pctl(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main():
    ap = argparse.ArgumentParser(description="Per-trade MAE/MFE/drawdown analyzer (read-only)")
    ap.add_argument("--days", type=int, default=30, help="Look back this many days (default 30)")
    ap.add_argument("--from", dest="dfrom", type=str, default=None, help="Start date YYYY-MM-DD (overrides --days)")
    ap.add_argument("--to", dest="dto", type=str, default=None, help="End date YYYY-MM-DD (default: now)")
    ap.add_argument("--symbol", type=str, default=None, help="Only this symbol (substring match, e.g. EURUSD)")
    ap.add_argument("--csv", type=str, default=None, help="CSV output path (default reports/mae_mfe_<stamp>.csv)")
    ap.add_argument("--pad-hours", type=float, default=15.0, help="Bar-window padding for broker-tz safety (default 15h)")
    ap.add_argument("--buffer-min", type=int, default=1, help="Minutes of slack around entry/exit (default 1)")
    args = ap.parse_args()

    from axonai.default_config import DEFAULT_CONFIG
    config = DEFAULT_CONFIG.copy()

    if args.dto:
        dto = datetime.strptime(args.dto, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    else:
        dto = datetime.now()
    if args.dfrom:
        dfrom = datetime.strptime(args.dfrom, "%Y-%m-%d")
    else:
        dfrom = dto - timedelta(days=args.days)

    connect(config)
    import MetaTrader5 as mt5

    positions = reconstruct_positions(mt5, dfrom, dto)
    if args.symbol:
        positions = [p for p in positions if args.symbol.upper() in p["symbol"].upper()]
    if not positions:
        print("No closed trades found in that window.")
        return

    buffer_s = args.buffer_min * 60
    rows = []
    for p in positions:
        mae, mfe, nb = mae_mfe(mt5, p, args.pad_hours, buffer_s)
        pip = pip_size(p["symbol"])
        realized = ((p["exit_price"] - p["entry_price"]) / pip) if p["direction"] == "BUY" \
            else ((p["entry_price"] - p["exit_price"]) / pip)
        mpp = money_per_pip(p["symbol"], p["volume"], p["exit_price"], p["gross"], realized)
        net = p["gross"] + p["commission"] + p["swap"]
        capture = (realized / mfe) if mfe > 0.01 else (1.0 if realized >= 0 else 0.0)
        rows.append({
            "close_time_utc": datetime.utcfromtimestamp(p["exit_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": p["symbol"], "dir": p["direction"], "vol": p["volume"],
            "entry": p["entry_price"], "exit": p["exit_price"],
            "realized_pips": round(realized, 1),
            "MAE_pips": round(mae, 1), "MFE_pips": round(mfe, 1),
            "give_back_pips": round(max(0.0, mfe - realized), 1),
            "capture_pct": round(100.0 * capture, 0),
            "MAE_money": round(mae * mpp, 2), "MFE_money": round(mfe * mpp, 2),
            "gross": round(p["gross"], 2), "comm": round(p["commission"], 2),
            "swap": round(p["swap"], 2), "net": round(net, 2), "bars": nb,
        })

    # ---- per-trade table ----
    print("\n=== PER-TRADE MAE / MFE (from MT5 history, %d trades) ===" % len(rows))
    print("  close (UTC)          sym       dir  vol   real  MAE   MFE  givebk cap%   net    ")
    for r in rows:
        low_res = "" if r["bars"] >= 2 else "  <-- few bars (low-res)"
        print("  %s  %-9s %-4s %.2f %+6.1f %5.1f %5.1f %6.1f %4.0f %+7.2f%s" % (
            r["close_time_utc"], r["symbol"], r["dir"], r["vol"], r["realized_pips"],
            r["MAE_pips"], r["MFE_pips"], r["give_back_pips"], r["capture_pct"], r["net"], low_res))

    # ---- summary for tuning ----
    mae = [r["MAE_pips"] for r in rows]
    mfe = [r["MFE_pips"] for r in rows]
    give = [r["give_back_pips"] for r in rows]
    cap = [r["capture_pct"] for r in rows]
    net = [r["net"] for r in rows]
    wins = [r for r in rows if r["net"] > 0]
    losses = [r for r in rows if r["net"] < 0]

    def avg(v):
        return sum(v) / len(v) if v else 0.0

    print("\n=== SUMMARY (for tuning) ===")
    print("  trades: %d   net winners: %d   net losers: %d   total net: %+.2f"
          % (len(rows), len(wins), len(losses), sum(net)))
    print("  MAE  (heat taken)   avg %.1f  median %.1f  p75 %.1f  p90 %.1f  worst %.1f pips"
          % (avg(mae), pctl(mae, 50), pctl(mae, 75), pctl(mae, 90), max(mae) if mae else 0))
    print("  MFE  (best offered) avg %.1f  median %.1f  p75 %.1f  p90 %.1f  best  %.1f pips"
          % (avg(mfe), pctl(mfe, 50), pctl(mfe, 75), pctl(mfe, 90), max(mfe) if mfe else 0))
    print("  give-back (MFE-realized): avg %.1f  median %.1f pips  (favorable move left on the table)"
          % (avg(give), pctl(give, 50)))
    print("  capture (realized/MFE):   avg %.0f%%  median %.0f%%" % (avg(cap), pctl(cap, 50)))

    heat_gt_5 = sum(1 for m in mae if m > 5)
    giveback_gt_5 = sum(1 for g in give if g > 5)
    print("\n  TUNING SIGNALS:")
    print("   * %d/%d trades took >5 pips of heat (MAE) before working out%s"
          % (heat_gt_5, len(rows), " - stops may be wider than needed" if avg(mae) < 4 else ""))
    print("   * %d/%d trades gave back >5 pips vs their best (MFE) - TP/trailing may be too tight"
          % (giveback_gt_5, len(rows)))
    if wins:
        print("   * winners: avg MAE %.1f, avg MFE %.1f, avg capture %.0f%%"
              % (avg([r["MAE_pips"] for r in wins]), avg([r["MFE_pips"] for r in wins]),
                 avg([r["capture_pct"] for r in wins])))
    if losses:
        print("   * losers:  avg MAE %.1f, avg MFE %.1f  (how much they offered before failing)"
              % (avg([r["MAE_pips"] for r in losses]), avg([r["MFE_pips"] for r in losses])))

    # per symbol
    print("\n  BY SYMBOL:")
    for s in sorted({r["symbol"] for r in rows}):
        rs = [r for r in rows if r["symbol"] == s]
        print("   %-9s trades %2d  avg MAE %.1f  avg MFE %.1f  net %+.2f"
              % (s, len(rs), avg([r["MAE_pips"] for r in rs]),
                 avg([r["MFE_pips"] for r in rs]), sum(r["net"] for r in rs)))

    # ---- CSV ----
    stamp = dto.strftime("%Y%m%d_%H%M%S")
    csv_path = args.csv or os.path.join("reports", "mae_mfe_%s.csv" % stamp)
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("\n  CSV written: %s" % csv_path)


if __name__ == "__main__":
    main()
