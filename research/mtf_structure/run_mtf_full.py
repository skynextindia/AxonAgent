"""Full-stack MTF structure: macro (daily) + micro (intraday), 5Y down to 5M.

    python -m research.mtf_structure.run_mtf_full

Macro frames from daily bars; intraday frames from native hourly/15m/5m bars.
Read-only. Writes out/mtf_full_snapshot.json.
"""
from __future__ import annotations

import csv, os, json
from .structure import classify_tf, MTFSnapshot

PIP = 0.0001
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")
OUT = os.path.join(_THIS, "out")


def load(fn):
    H = []; L = []; C = []; T = []
    with open(os.path.join(_ROOT, fn)) as f:
        for r in csv.DictReader(f):
            try:
                H.append(float(r["High"])); L.append(float(r["Low"])); C.append(float(r["Close"]))
                T.append(r.get("Datetime") or r.get("Date"))
            except Exception:
                continue
    return H, L, C, T


def main() -> int:
    dH, dL, dC, dT = load("eurusd_daily_10y.csv")
    hH, hL, hC, hT = load("eurusd_h1_2y.csv")
    mH, mL, mC, mT = load("eurusd_m15_recent60d.csv")
    fH, fL, fC, fT = load("eurusd_5m.csv")
    cur = fC[-1]           # most-current price (5m feed)
    ts = fT[-1]

    # (name, highs, lows, closes, bars)  — top(slow) to bottom(fast)
    plan = [
        ("5Y", dH, dL, dC, 1260), ("1Y", dH, dL, dC, 252),
        ("3M", dH, dL, dC, 63),  ("1M", dH, dL, dC, 21), ("1W", dH, dL, dC, 5),
        ("1D", hH, hL, hC, 24),  ("1H", hH, hL, hC, 12),
        ("15M", mH, mL, mC, 24), ("5M", fH, fL, fC, 24),
    ]
    tfs = []
    for name, H, L, C, bars in plan:
        tf = classify_tf(name, H, L, C, cur, PIP, bars)
        if tf is not None:
            tfs.append(tf)
    snap = MTFSnapshot(price=round(cur, 5), tfs=tfs)

    print(f"EURUSD FULL MTF STRUCTURE  |  {ts}  price {cur:.5f}\n")
    print(f"{'TF':4s} {'trend':6s} {'net':>8s} {'ER':>5s} {'range hi/lo':>19s} {'pos':>5s}  where")
    for t in tfs:
        b = "#" * int(t.position_pct / 10) + "." * (10 - int(t.position_pct / 10))
        print(f"{t.name:4s} {t.trend:6s} {t.net_pips:+8.0f} {t.efficiency_ratio:5.2f}  "
              f"{t.range_hi:.4f}/{t.range_lo:.4f} [{b}]{t.position_pct:4.0f}%  {t.position_label}")

    # alignment summary
    macro = [t for t in tfs if t.name in ("5Y", "1Y", "3M")]
    micro = [t for t in tfs if t.name in ("1H", "15M", "5M")]
    def dom(g):
        u = sum(1 for t in g if t.trend == "UP"); d = sum(1 for t in g if t.trend == "DOWN")
        return "UP" if u > d and u > len(g)-u-d else "DOWN" if d > u and d > len(g)-u-d else "MIXED/RANGE"
    up = sum(1 for t in tfs if t.trend == "UP"); dn = sum(1 for t in tfs if t.trend == "DOWN")
    print(f"\nFRACTAL READ: {snap.summary()}")
    print(f"  macro(5Y-3M)={dom(macro)}   micro(1H-5M)={dom(micro)}   "
          f"({up} UP / {dn} DOWN / {len(tfs)-up-dn} RANGE)")
    print(f"  >>> {snap.fade_read()['note']}")

    pd = snap.premium_discount()
    print(f"\nPREMIUM / DISCOUNT (buy discounts, sell premiums):")
    print(f"  MACRO (5Y-3M): {pd['macro_pos']:.0f}% -> {pd['macro_zone'].upper()}")
    print(f"  INTRADAY (1D-5M): {pd['intraday_pos']:.0f}% -> {pd['intraday_zone'].upper()}")
    print(f"  BIAS: {pd['bias']}")
    print(f"  TIMING: {pd['timing']}")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "mtf_full_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, indent=1)
    print("\nwrote out/mtf_full_snapshot.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
