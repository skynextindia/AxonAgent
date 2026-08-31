"""Does a SELL fail when price has crossed ABOVE the previous day's close and kept
climbing? (user hypothesis 2026-08-31)

USER CLAIM: on a negative open (day opens below prior close) that then CLIMBS, our
sells get pressed/stopped exactly where price crosses the previous day's close and
continues the up-trend. So prevClose is a trend-confirmation pivot: selling ABOVE it
(after crossing up through it) = fighting a confirmed up-move = a losing sell.

TEST on REAL MT5 M15 (2.4y). For each fade SELL entry, tag its location vs the prior
trading day's CLOSE (the pivot):
  above_pc     = entry price > prevClose            (in 'up' territory)
  neg_open     = day opened below prevClose
  crossed_up   = neg_open AND above_pc              (opened negative, climbed through)
Then compare SELL net expectancy (bracket sim, net of cost, on M15) across buckets,
bucketed by quarter, and test the gate "skip SELLs above prevClose".

    python -m research.prevclose_bias.prevclose_sell_gate
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001
COST = 2.0
SWING_K = 6
MAX_HOLD_BARS = int(120 * 60 / 15)      # 120h of M15 bars
_THIS = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m15_mt5.csv")


def load():
    bars = []
    with open(M15) as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(r["Datetime"].replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bars.append((dt.astimezone(timezone.utc), float(r["Open"]), float(r["High"]),
                         float(r["Low"]), float(r["Close"])))
    bars.sort(key=lambda x: x[0])
    return bars


def quarter(dt):
    return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def daily_frames(bars):
    """Per-UTC-date open (first bar open) and close (last bar close), + prevClose map."""
    day_open, day_close = {}, {}
    for dt, o, h, l, c in bars:
        d = dt.date()
        if d not in day_open:
            day_open[d] = o
        day_close[d] = c
    dates = sorted(day_close)
    prev_close = {}
    for k in range(1, len(dates)):
        prev_close[dates[k]] = day_close[dates[k - 1]]
    return day_open, prev_close


def build_short_entries(bars, day_open, prev_close):
    out = []
    for i in range(SWING_K, len(bars) - 1):
        dt, o, hi, lo, c = bars[i]
        win = bars[i - SWING_K:i + 1]
        is_high = hi >= max(b[2] for b in win)
        is_low = lo <= min(b[3] for b in win)
        if is_high == is_low or not is_high:        # SELL fades only (short a high)
            continue
        d = dt.date()
        pc = prev_close.get(d)
        if pc is None:
            continue
        out.append({"i": i, "q": quarter(dt), "entry": c, "pc": pc,
                    "above_pc": c > pc, "neg_open": day_open[d] < pc,
                    "dist_pc": (c - pc) / PIP})
    return out


def sim_short(bars, e, sl_p=20, tp_p=100):
    i = e["i"]; entry = e["entry"]; sl = sl_p * PIP; tp = tp_p * PIP
    n = len(bars); out = None
    for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
        _, _, bh, bl, bc = bars[j]
        hit_sl = bh >= entry + sl; hit_tp = bl <= entry - tp
        if hit_sl and hit_tp:
            out = -sl_p; break                       # conservative
        if hit_tp:
            out = tp_p; break
        if hit_sl:
            out = -sl_p; break
    if out is None:
        out = (entry - bars[min(i + MAX_HOLD_BARS, n - 1)][4]) / PIP
    return out - COST


def summ(name, vals_by_q):
    allv = [p for v in vals_by_q.values() for p in v]
    if not allv:
        print(f"  {name:42} n=0"); return
    per_q = {q: mean(v) for q, v in vals_by_q.items()}
    pos = sum(1 for x in per_q.values() if x > 0)
    print(f"  {name:42} n={len(allv):5d}  avg {mean(allv):+6.2f}p  {pos}/{len(per_q)} q+")


def bucket(entries, pred):
    by_q = defaultdict(list)
    for e in entries:
        if pred(e):
            by_q[e["q"]].append(e["net"])
    return by_q


def main() -> int:
    bars = load()
    day_open, prev_close = daily_frames(bars)
    shorts = build_short_entries(bars, day_open, prev_close)
    for sl, tp in ((20, 100), (20, 20)):
        for e in shorts:
            e["net"] = sim_short(bars, e, sl, tp)
        print(f"\n================ SELL fades, bracket SL{sl}/TP{tp}, cost {COST}p ================")
        print(f"  ({len(shorts)} sell entries, {bars[0][0].date()}..{bars[-1][0].date()})")
        summ("ALL sells", bucket(shorts, lambda e: True))
        print("  -- location vs PREVIOUS DAY CLOSE (the pivot) --")
        summ("sells BELOW prevClose (down territory)", bucket(shorts, lambda e: not e["above_pc"]))
        summ("sells ABOVE prevClose (up territory)", bucket(shorts, lambda e: e["above_pc"]))
        print("  -- the user's exact pattern --")
        summ("neg-open & CLIMBED above prevClose", bucket(shorts, lambda e: e["neg_open"] and e["above_pc"]))
        summ("everything else", bucket(shorts, lambda e: not (e["neg_open"] and e["above_pc"])))
        print("  -- distance ABOVE prevClose --")
        summ("0-10p above prevClose", bucket(shorts, lambda e: 0 <= e["dist_pc"] < 10))
        summ("10-25p above prevClose", bucket(shorts, lambda e: 10 <= e["dist_pc"] < 25))
        summ("25p+ above prevClose", bucket(shorts, lambda e: e["dist_pc"] >= 25))
        print("  -- GATE: skip sells above prevClose (keep only below) --")
        summ("GATED sells (below prevClose only)", bucket(shorts, lambda e: not e["above_pc"]))

    print("\nREAD: if 'sells ABOVE prevClose' and the neg-open-climbed bucket are much")
    print("worse than 'sells BELOW prevClose', the user's pivot factor is real and the")
    print("gate (skip sells above prevClose) improves the fade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
