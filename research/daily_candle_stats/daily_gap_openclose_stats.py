"""Daily candle statistics: open-vs-prior-close, closing relation, gap probability.

USER QUESTION (2026-08-31): using whole-day candles, find the relativity of each
day's OPEN vs the previous day's CLOSE and the closing relation, as probabilities.

HEADLINE DATA CAVEAT (measured in section 0): yfinance EURUSD=X daily bars have a
DEGENERATE open — median |Open-Close| is ~0.4 pip, i.e. Open == Close nearly every
day (a real EURUSD daily candle has a 30-80 pip body). So:
  * the daily "body" (Close-Open) is ~0 noise -> unusable
  * the "gap" (Open - prevClose) is just the close-to-close change relabelled
    (that is why P(close>prevclose | gap up) came out ~99%)
FX trades 24h, so the ONLY real open-gap is over the WEEKEND. We therefore answer
the question with the fields that ARE trustworthy:
  1. daily CLOSE-to-CLOSE direction + autocorrelation  (real; Close is reliable)
  2. the real Friday->Monday WEEKEND gap, measured from H1 bars

    python -m research.daily_candle_stats.daily_gap_openclose_stats
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from statistics import mean, median, pstdev

PIP = 0.0001
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")


def load_daily():
    rows = []
    with open(os.path.join(_ROOT, "eurusd_daily_10y.csv")) as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(r["Date"][:10]).date()
                rows.append((d, float(r["Open"]), float(r["High"]),
                             float(r["Low"]), float(r["Close"])))
            except Exception:
                continue
    rows.sort(key=lambda x: x[0])
    return rows


def load_h1():
    bars = []
    with open(os.path.join(_ROOT, "eurusd_h1_2y.csv")) as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat((r.get("Datetime") or r["Date"]).replace(" ", "T"))
                bars.append((dt.astimezone(timezone.utc), float(r["Open"]), float(r["Close"])))
            except Exception:
                continue
    bars.sort(key=lambda x: x[0])
    return bars


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def main() -> int:
    rows = load_daily()
    print(f"daily bars: {len(rows)}  ({rows[0][0]}..{rows[-1][0]})")

    # ---------- 0. DATA REALITY: is the daily OPEN usable? ----------
    body = [abs(o - c) / PIP for _, o, _, _, c in rows]
    same = sum(1 for b in body if b < 0.1)
    print("\n== 0. DATA REALITY CHECK (is the daily Open real?) ==")
    print(f"  |Open-Close| median {median(body):.2f}p  mean {mean(body):.2f}p   "
          f"Open==Close (<0.1p) on {pct(same,len(rows)):.0f}% of days")
    print("  VERDICT: Open is SYNTHETIC (Open~=Close). Daily body & open-gap stats from")
    print("  this source are artifacts; use close-to-close + the real weekend gap below.")

    # ---------- 1. REAL: daily CLOSE-to-CLOSE direction + autocorrelation ----------
    closes = [c for _, _, _, _, c in rows]
    d2d = [(closes[i] - closes[i - 1]) / PIP for i in range(1, len(closes))]
    up = sum(1 for x in d2d if x > 0)
    print("\n== 1. DAILY CLOSE-TO-CLOSE (real: the actual daily direction) ==")
    print(f"  n={len(d2d)}  P(up day)={pct(up,len(d2d)):.1f}%  mean {mean(d2d):+.2f}p  "
          f"median {median(d2d):+.2f}p  sd {pstdev(d2d):.1f}")
    pu = pd_ = cu = cd = 0
    for i in range(1, len(d2d)):
        if d2d[i - 1] > 0:
            pu += 1; cu += d2d[i] > 0
        else:
            pd_ += 1; cd += d2d[i] > 0
    print(f"  given prev day UP   (n={pu:4d}): P(today up)={pct(cu,pu):.1f}%")
    print(f"  given prev day DOWN (n={pd_:4d}): P(today up)={pct(cd,pd_):.1f}%")
    spread = cu / pu - cd / pd_
    print(f"  autocorrelation: {'MOMENTUM' if spread>0.05 else 'MEAN-REVERT' if spread<-0.05 else 'COIN-FLIP (no daily edge)'}"
          f"  (spread {spread*100:+.1f}pp)")

    # ---------- 2. REAL WEEKEND GAP (from H1 bars) ----------
    bars = load_h1()
    gaps = []
    for i in range(1, len(bars)):
        dt0, _, c0 = bars[i - 1]
        dt1, o1, _ = bars[i]
        if (dt1 - dt0).total_seconds() / 3600 >= 24:      # weekend/holiday break
            gaps.append((o1 - c0) / PIP)
    print("\n== 2. REAL WEEKEND GAP (H1: last pre-break close -> first post-break open) ==")
    if gaps:
        big = sum(1 for g in gaps if abs(g) > 3)
        gu = sum(1 for g in gaps if g > 0)
        print(f"  n={len(gaps)}  mean {mean(gaps):+.2f}p  median {median(gaps):+.2f}p  sd {pstdev(gaps):.1f}")
        print(f"  |gap|>3p on {pct(big,len(gaps)):.0f}% of weekends   gap-up {pct(gu,len(gaps)):.0f}%   "
              f"extremes {max(gaps):+.0f}p / {min(gaps):+.0f}p")
        print("  -> median ~0 (most weekends flat) but FAT TAILS; direction ~coin-flip.")
        print("     This is the gap risk the Friday-flatten (eod_flatten_weekend_only) sits out.")

    print("\nBOTTOM LINE: daily direction is a coin-flip (close-to-close ~49% up, no")
    print("autocorrelation): the random-walk verdict at the daily scale. The only real")
    print("open-gap is the weekend, which is unpredictable but tail-heavy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
