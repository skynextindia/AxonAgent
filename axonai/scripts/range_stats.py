"""Daily-range + reversal-zone statistics per pair, from the engine snapshot store.

For each trading day in reports/engine_snapshots_{symbol}.csv computes:
  high / low / range_pips / path_pips (total distance price actually walked)
and across days:
  ADR5 / ADR20 (rolling average daily range),
  reversal sizes (median/avg pips of detected turns), and
  WHERE in the day's range reversals form (decile histogram, 0=day low, 1=day high).

Output: reports/range_stats_{symbol}.json — loaded by the daemon at startup so
live logic knows "how much of a normal day is already used" (range_used) and
"where price sits in today's range" (range_pos). Run daily via calibrate_all.

Usage:  python -m axonai.scripts.range_stats --symbol EURUSD
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict
from typing import Dict, List

from axonai.scripts.eod_reversal_analysis import find_reversals, _pip_mult, _f

# Reversal thresholds per pair (pips) — same spirit as calibrate_all.
REV_TH = {"XAUUSD": 120.0, "USDJPY": 12.0}
REV_TH_DEFAULT = 8.0


def compute(symbol: str, keep_days: int = 30) -> Dict:
    import glob
    pip = _pip_mult(symbol)
    # Include rotated stores (*_old_*, *_pre_location) so a schema-rotation on
    # restart doesn't throw away the accumulated history the stats need.
    paths = sorted(glob.glob(os.path.join("reports", f"engine_snapshots_{symbol}*.csv")))
    if not paths:
        print(f"[range] no snapshot store for {symbol}")
        return {}
    rows: List[dict] = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", newline="") as f:
                rows.extend(csv.DictReader(f))
        except Exception:
            continue
    rows = [r for r in rows if r.get("timestamp") and r.get("price")]
    rows.sort(key=lambda r: r.get("timestamp") or "")
    if len(rows) < 100:
        print(f"[range] {symbol}: too few rows ({len(rows)})")
        return {}

    # ── group by day ────────────────────────────────────────────────
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        d = (r.get("timestamp") or "")[:10]
        if d:
            by_day[d].append(r)

    days = []
    for d in sorted(by_day.keys())[-keep_days:]:
        drows = by_day[d]
        prices = [_f(r, "price") for r in drows if _f(r, "price") > 0]
        if len(prices) < 50:
            continue
        hi, lo = max(prices), min(prices)
        path = sum(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
        days.append({
            "date": d, "high": round(hi, 5), "low": round(lo, 5),
            "range_pips": round((hi - lo) / pip, 1),
            "path_pips": round(path / pip, 1),
            "ticks": len(prices),
        })
    if not days:
        return {}

    ranges = [d["range_pips"] for d in days]
    adr5 = round(statistics.mean(ranges[-5:]), 1)
    adr20 = round(statistics.mean(ranges[-20:]), 1)
    avg_day = statistics.mean(ranges)

    # ── per-session average range + session/day ratio ──────────────
    # UTC session windows (match the dashboard session bands). A session's
    # range = high-low of prices inside its window on that day, averaged.
    SESSIONS = {"ASIA": (0, 8), "LONDON": (7, 16), "NY": (12, 21)}
    counted = {d["date"] for d in days}
    sess_vals = {s: [] for s in SESSIONS}
    for d, drows in by_day.items():
        if d not in counted:
            continue
        for s, (a, b) in SESSIONS.items():
            sp = []
            for r in drows:
                p = _f(r, "price")
                if p <= 0:
                    continue
                ts = r.get("timestamp") or ""
                try:
                    hh = int(ts[11:13])
                except (ValueError, IndexError):
                    continue
                if a <= hh < b:
                    sp.append(p)
            if len(sp) >= 20:
                sess_vals[s].append((max(sp) - min(sp)) / pip)
    sessions_out = {}
    for s, vals in sess_vals.items():
        if vals:
            avg = statistics.mean(vals)
            sessions_out[s] = {
                "avg_pips": round(avg, 1),
                "ratio": round(avg / avg_day, 2) if avg_day else 0.0,  # session vs full-day range
                "days": len(vals),
            }

    # ── reversal sizes + WHERE in the day range turns form ─────────
    th = REV_TH.get(symbol.upper(), REV_TH_DEFAULT)
    events = find_reversals(rows, pip, th)
    rev_sizes: List[float] = []
    zone_hist = [0] * 10  # deciles of day range; 0 = at day low, 9 = at day high
    day_bounds = {d["date"]: (d["low"], d["high"]) for d in days}
    for i, e in enumerate(events):
        r = rows[e["idx"]]
        d = (r.get("timestamp") or "")[:10]
        if d not in day_bounds:
            continue
        lo, hi = day_bounds[d]
        if hi <= lo:
            continue
        pos = max(0.0, min(0.999, (e["price"] - lo) / (hi - lo)))
        zone_hist[int(pos * 10)] += 1
        if i + 1 < len(events):
            rev_sizes.append(abs(events[i + 1]["price"] - e["price"]) / pip)

    out = {
        "symbol": symbol,
        "adr5": adr5,
        "adr20": adr20,
        "avg_range_pips": round(statistics.mean(ranges), 1),
        "avg_path_pips": round(statistics.mean(d["path_pips"] for d in days), 1),
        "reversal_count": len(events),
        "reversal_median_pips": round(statistics.median(rev_sizes), 1) if rev_sizes else 0.0,
        "reversal_avg_pips": round(statistics.mean(rev_sizes), 1) if rev_sizes else 0.0,
        "reversal_zone_hist": zone_hist,   # deciles low->high
        "reversal_edge_share": round(sum(zone_hist[:2]) + sum(zone_hist[8:]), 0) and round(
            (sum(zone_hist[:2]) + sum(zone_hist[8:])) / max(1, sum(zone_hist)), 2),
        "sessions": sessions_out,          # per-session avg range + session/day ratio
        "days": days,
    }
    path_out = os.path.join("reports", f"range_stats_{symbol}.json")
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"[range] {symbol}: ADR5={adr5} ADR20={adr20} revs={len(events)} "
          f"med_rev={out['reversal_median_pips']}p edge_share={out['reversal_edge_share']} -> {path_out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None, help="one symbol, or omit for all 5")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    syms = [args.symbol] if args.symbol else ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD"]
    for s in syms:
        compute(s, keep_days=args.days)


if __name__ == "__main__":
    main()
