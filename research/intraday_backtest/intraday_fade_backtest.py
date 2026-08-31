"""INTRADAY fade backtest on REAL MT5 bars — does the fade / wide-TP edge hold
at intraday resolution, and how sensitive is it to intra-bar exit ordering?

Entries: fade a local M15 swing (short a swing high / long a swing low) — the live
strategy's shape. Exits: a fixed (SL,TP) bracket, resolved TWO ways:
  * on M15 bars (full 2.4y sample)
  * on M5 bars (finer; the conservative-vs-optimistic gap nearly vanishes because a
    5-min bar rarely straddles both stop and target — this is the honest execution
    read the coarse H1 test in exit_asymmetry_regime_test.py could only bound).
Net of cost, bucketed by quarter. Brackets: the live scalp (20/20) and the wide
profile (20/100, 1:5) plus neighbours.

    python -m research.intraday_backtest.intraday_fade_backtest
"""
from __future__ import annotations
import csv, os, bisect
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001
COST = 2.0
SWING_K = 6            # M15 bars each side for a local swing (causal [i-K, i])
MAX_HOLD_H = 120       # ~5 trading days, matches the overnight/weekend hold
_THIS = os.path.dirname(os.path.abspath(__file__))


def load(path):
    bars = []
    with open(path) as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(r["Datetime"].replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bars.append((dt.astimezone(timezone.utc), float(r["High"]),
                         float(r["Low"]), float(r["Close"])))
    bars.sort(key=lambda x: x[0])
    return bars


def quarter(dt):
    return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def build_entries(m15):
    """fade: short a local swing high, long a local swing low."""
    out = []
    for i in range(SWING_K, len(m15) - 1):
        dt, hi, lo, c = m15[i]
        win = m15[i - SWING_K:i + 1]
        is_high = hi >= max(b[1] for b in win)
        is_low = lo <= min(b[2] for b in win)
        if is_high == is_low:
            continue
        out.append((dt, c, bool(is_high), quarter(dt)))   # short if is_high
    return out


def sim_on(series, entries, sl_p, tp_p, optimistic, tf_min):
    """Resolve each entry's bracket by scanning `series` (M5 or M15) after entry."""
    times = [b[0] for b in series]
    sl = sl_p * PIP; tp = tp_p * PIP
    max_hold = int(MAX_HOLD_H * 60 / tf_min)
    rows = []
    n = len(series)
    for dt, entry, short, q in entries:
        start = bisect.bisect_right(times, dt)
        outcome = None
        end = min(start + max_hold, n)
        for j in range(start, end):
            _, bh, bl, bc = series[j]
            if short:
                hit_sl = bh >= entry + sl; hit_tp = bl <= entry - tp
            else:
                hit_sl = bl <= entry - sl; hit_tp = bh >= entry + tp
            if hit_sl and hit_tp:
                outcome = tp_p if optimistic else -sl_p; break
            if hit_tp:
                outcome = tp_p; break
            if hit_sl:
                outcome = -sl_p; break
        if outcome is None:
            lastc = series[min(end, n) - 1][3]
            outcome = ((entry - lastc) if short else (lastc - entry)) / PIP
        rows.append((q, outcome - COST))
    return rows


def agg_by_q(rows):
    byq = defaultdict(list)
    for q, p in rows:
        byq[q].append(p)
    per_q = {q: mean(v) for q, v in byq.items()}
    overall = mean([p for _, p in rows]) if rows else 0.0
    posq = sum(1 for v in per_q.values() if v > 0)
    return overall, posq, len(per_q)


GRID = [(20, 20), (20, 60), (20, 80), (20, 100), (15, 60)]


def report(name, series, entries, tf_min):
    print(f"\n==================== EXIT RESOLUTION: {name} ====================")
    print(f"  {'bracket':12}{'CONS net':>10}{'q+':>6}   {'OPTI net':>10}{'q+':>6}   (gross add cost back = +2p)")
    for sl, tp in GRID:
        rc = sim_on(series, entries, sl, tp, False, tf_min)
        ro = sim_on(series, entries, sl, tp, True, tf_min)
        oc, pc, nq = agg_by_q(rc)
        oo, po, _ = agg_by_q(ro)
        star = "  <-- live scalp" if (sl, tp) == (20, 20) else ("  <-- wide 1:5" if (sl, tp) == (20, 100) else "")
        print(f"  SL{sl}/TP{tp:<7}{oc:>+9.2f}p{pc:>4}/{nq}   {oo:>+9.2f}p{po:>4}/{nq}{star}")


def main() -> int:
    m15 = load(os.path.join(_THIS, "eurusd_m15_mt5.csv"))
    m5p = os.path.join(_THIS, "eurusd_m5_mt5.csv")
    m5 = load(m5p) if os.path.exists(m5p) else None
    entries = build_entries(m15)
    print(f"M15 bars {len(m15)} ({m15[0][0].date()}..{m15[-1][0].date()})  fade entries {len(entries)}")
    print(f"cost={COST}p  swing_k={SWING_K} (M15)  max_hold={MAX_HOLD_H}h")

    # M15-resolution over the full sample
    report(f"M15 bars — full sample ({len(entries)} entries)", m15, entries, 15)

    # M5-resolution over the overlap window (finer intra-bar ordering)
    if m5:
        cutoff = m5[0][0]
        e5 = [e for e in entries if e[0] >= cutoff]
        print(f"\nM5 sample starts {cutoff.date()}; entries in M5 window: {len(e5)}")
        report(f"M5 bars — {m5[0][0].date()}..{m5[-1][0].date()} ({len(e5)} entries)", m5, e5, 5)
        print("\n  NOTE: if CONS and OPTI nearly coincide on M5, the intra-bar ambiguity is")
        print("  resolved and the number is the real execution outcome (not a bound).")

    print("\nREAD vs [[expectancy-verdict]] (H1, +0.98p wide at limit cost): confirm the")
    print("narrow 20/20 scalp stays negative and whether wide 20/100 clears cost intraday.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
