"""Build + print the current EURUSD MTF structure from the daily history.

    python -m research.mtf_structure.run_mtf_structure

Reads eurusd_daily_10y.csv (read-only), prints the top-down snapshot, and writes
out/mtf_snapshot.json.
"""
from __future__ import annotations

import csv, os, json
from .structure import compute_mtf

PIP = 0.0001
_THIS = os.path.dirname(os.path.abspath(__file__))
_CSV = os.path.join(_THIS, "..", "..", "eurusd_daily_10y.csv")
OUT = os.path.join(_THIS, "out")


def load():
    H = []; L = []; C = []; dates = []
    with open(_CSV) as f:
        for r in csv.DictReader(f):
            try:
                H.append(float(r["High"])); L.append(float(r["Low"]))
                C.append(float(r["Close"])); dates.append(r["Date"][:10])
            except Exception:
                continue
    return H, L, C, dates


def main() -> int:
    H, L, C, dates = load()
    snap = compute_mtf(H, L, C, PIP)
    print(f"EURUSD MTF STRUCTURE  |  {dates[-1]}  price {snap.price:.5f}\n")
    print(f"{'TF':4s} {'trend':6s} {'net':>8s} {'ER':>5s} {'range hi/lo':>19s} {'pos':>5s}  where")
    for t in snap.tfs:
        bar = "#" * int(t.position_pct / 10) + "." * (10 - int(t.position_pct / 10))
        print(f"{t.name:4s} {t.trend:6s} {t.net_pips:+8.0f} {t.efficiency_ratio:5.2f}  "
              f"{t.range_hi:.4f}/{t.range_lo:.4f} [{bar}]{t.position_pct:4.0f}%  {t.position_label}")
    fr = snap.fade_read()
    print(f"\nSTRUCTURAL READ: {snap.summary()}")
    print(f"  short-term momentum: {fr['short_tf_momentum']}   "
          f"higher-TF top-extreme: {fr['higher_tf_extreme_top']}")
    print(f"  >>> {fr['note']}")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "mtf_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, indent=1)
    print("\nwrote out/mtf_snapshot.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
