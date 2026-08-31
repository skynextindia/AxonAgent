"""Daily candle statistics on REAL MT5 bars (eurusd_d1_mt5.csv).

Answers the user's question with a trustworthy open: each day's OPEN vs the prior
CLOSE (gap), the day BODY (open->close), gap-fill, and day-over-day relations, as
conditional probabilities. Run fetch_mt5_daily.py first to (re)create the CSV.

    python -m research.daily_candle_stats.daily_stats_mt5
"""
from __future__ import annotations
import csv, os
from datetime import datetime
from statistics import mean, median, pstdev

PIP = 0.0001
_THIS = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(_THIS, "eurusd_d1_mt5.csv")


def load():
    rows = []
    with open(CSV) as f:
        for r in csv.DictReader(f):
            d = datetime.fromisoformat(r["Date"][:10]).date()
            rows.append((d, float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])))
    rows.sort(key=lambda x: x[0])
    return rows


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def desc(v):
    return (f"n={len(v)} mean {mean(v):+.1f}p median {median(v):+.1f}p sd {pstdev(v):.1f} "
            f"[{min(v):+.0f}..{max(v):+.0f}]") if v else "n=0"


def main() -> int:
    rows = load()
    print(f"REAL MT5 daily bars: {len(rows)}  ({rows[0][0]}..{rows[-1][0]})")

    recs = []
    for k in range(1, len(rows)):
        d, o, hi, lo, c = rows[k]
        _, po, _, _, pc = rows[k - 1]
        recs.append({"wd": d.weekday(), "o": o, "hi": hi, "lo": lo, "c": c, "pc": pc,
                     "gap": (o - pc) / PIP, "body": (c - o) / PIP, "d2d": (c - pc) / PIP,
                     "filled": lo <= pc <= hi, "gap_up": o > pc})

    # 0. body reality
    body = [abs(r["body"]) for r in recs]
    print(f"\n== 0. body reality: |Open-Close| median {median(body):.1f}p (real candle, not synthetic) ==")

    # 1. GAP: open vs prior close
    print("\n== 1. GAP (Open vs prior Close) ==")
    g = [r["gap"] for r in recs]
    gu = sum(r["gap_up"] for r in recs)
    print(f"  {desc(g)}")
    print(f"  P(gap UP)={pct(gu,len(recs)):.1f}%  P(gap DOWN)={pct(len(recs)-gu,len(recs)):.1f}%")
    mon = [r["gap"] for r in recs if r["wd"] == 0]
    wkd = [r["gap"] for r in recs if r["wd"] != 0]
    print(f"  MONDAY (weekend) gap: {desc(mon)}")
    print(f"  WEEKDAY gap:          {desc(wkd)}")

    # 2. BODY: open -> close
    print("\n== 2. DAY BODY (Open -> Close) ==")
    bu = sum(1 for r in recs if r["body"] > 0)
    print(f"  P(close>open)={pct(bu,len(recs)):.1f}%   {desc([r['body'] for r in recs])}")

    # 3. does the GAP predict the day?
    print("\n== 3. CONDITIONAL: does gap direction predict the day? ==")
    for label, up in (("gap UP", True), ("gap DOWN", False)):
        sub = [r for r in recs if r["gap_up"] == up]
        cont = sum(1 for r in sub if (r["body"] > 0) == up)   # body continues the gap
        clo = sum(1 for r in sub if (r["d2d"] > 0) == up)     # close stays past prevclose in gap dir
        print(f"  after {label:9} (n={len(sub):4d}): P(body continues gap)={pct(cont,len(sub)):.1f}%  "
              f"P(close finishes gap side)={pct(clo,len(sub)):.1f}%  mean body {mean([r['body'] for r in sub]):+.1f}p")

    # 4. GAP FILL
    print("\n== 4. GAP FILL (does price trade back to prior close intraday) ==")
    ng = [r for r in recs if abs(r["gap"]) >= 3]
    fl = sum(1 for r in ng if r["filled"])
    print(f"  among gaps |gap|>=3p (n={len(ng)}): filled same day {pct(fl,len(ng)):.1f}%")
    for label, up in (("gap UP", True), ("gap DOWN", False)):
        sub = [r for r in ng if r["gap_up"] == up]
        if sub:
            print(f"    {label:9}: fill {pct(sum(r['filled'] for r in sub),len(sub)):.1f}%  (n={len(sub)})")

    # 5. day-over-day close autocorrelation
    print("\n== 5. DAY-OVER-DAY CLOSE (momentum vs mean-reversion) ==")
    du = sum(1 for r in recs if r["d2d"] > 0)
    print(f"  P(close>prevclose)={pct(du,len(recs)):.1f}%")
    d2d = [r["d2d"] for r in recs]
    pu = pd_ = cu = cd = 0
    for i in range(1, len(d2d)):
        if d2d[i - 1] > 0:
            pu += 1; cu += d2d[i] > 0
        else:
            pd_ += 1; cd += d2d[i] > 0
    print(f"  given prev close-day UP   (n={pu:4d}): P(today up)={pct(cu,pu):.1f}%")
    print(f"  given prev close-day DOWN (n={pd_:4d}): P(today up)={pct(cd,pd_):.1f}%")
    sp = cu / pu - cd / pd_
    print(f"  autocorrelation: {'MOMENTUM' if sp>0.05 else 'MEAN-REVERT' if sp<-0.05 else 'COIN-FLIP'} (spread {sp*100:+.1f}pp)")

    # 6. weekday seasonality of the body
    print("\n== 6. BODY by weekday ==")
    for wd, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
        sub = [r for r in recs if r["wd"] == wd]
        if sub:
            up = sum(1 for r in sub if r["body"] > 0)
            print(f"  {nm}: P(close>open)={pct(up,len(sub)):5.1f}%  mean body {mean([r['body'] for r in sub]):+.1f}p  (n={len(sub)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
