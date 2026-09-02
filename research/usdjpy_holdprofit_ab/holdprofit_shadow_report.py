"""Reader for the live USDJPY hold-for-profit forward shadow.

Each row in reports/usdjpy_holdprofit_shadow.jsonl is one real closed USDJPY leg with
a_pips  = the ACTUAL default-policy result (breakeven scratch + structure-trail), and
b_pips  = the counterfactual hold_for_profit exit (breakeven OFF, wide/late ATR trail)
measured on the SAME live tick path. This scores B vs A forward, apples-to-apples,
without having traded B — the clean answer the NO-GO sim (ab_sim.py) could not give
because it couldn't reproduce live policy-A.

    python -m research.usdjpy_holdprofit_ab.holdprofit_shadow_report [path]

DECISION: arm hold_for_profit for USDJPY only if B beats A forward (net + median),
across >=2 regimes, on real n (>=~30). Until then the early scratch stays (protective).
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from statistics import mean, median

DEFAULT = os.path.join("reports", "usdjpy_holdprofit_shadow.jsonl")


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("type") == "holdprofit":
                    rows.append(r)
            except Exception:
                continue
    return rows


def summ(tag, vals):
    if not vals:
        print("  %-22s n=0" % tag); return
    n = len(vals)
    print("  %-22s n=%3d  sum%+8.1f  mean%+6.2f  median%+6.2f  win%3.0f%%" % (
        tag, n, sum(vals), mean(vals), median(vals),
        100 * sum(1 for v in vals if v > 0) / n))


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    rows = load(path)
    print("USDJPY hold-for-profit forward shadow: %d closed legs from %s" % (len(rows), path))
    if not rows:
        print("  (no rows yet: written on each real USDJPY leg CLOSE. Needs a flat")
        print("   restart to activate + mirror legs to close. Re-check as they accrue.)")
        return 0
    a = [r["a_pips"] for r in rows]
    b = [r["b_pips"] for r in rows]
    print("\n== overall (gross pips) ==")
    summ("A = default (live)", a)
    summ("B = hold-for-profit", b)
    d = [r["delta_b_minus_a"] for r in rows]
    print("  delta B-A: sum %+.1f  mean %+.2f  median %+.2f  (B wins %d / %d legs)" % (
        sum(d), mean(d), median(d), sum(1 for x in d if x > 0), len(d)))

    print("\n== where B diverges from A (b_reason on the legs B changed) ==")
    byr = defaultdict(list)
    for r in rows:
        if r.get("b_reason") != "ACTUAL":
            byr[r["b_reason"]].append(r["delta_b_minus_a"])
    for reason, ds in sorted(byr.items()):
        print("  B exited %-8s on %d legs, delta sum %+.1f" % (reason, len(ds), sum(ds)))
    same = sum(1 for r in rows if r.get("b_reason") == "ACTUAL")
    print("  (%d legs: B never tripped in-window -> equals A)" % same)

    print("\nVERDICT GATE: arm hold_for_profit for USDJPY only if delta B-A is clearly")
    print("positive across >=2 regimes on real n>=~30. Sim said NO-GO; let live decide.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
