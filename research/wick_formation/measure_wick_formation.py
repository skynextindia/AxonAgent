"""READ-ONLY test of the user's wick-formation idea (2026-08-20):

Measure each candle's upper/lower SHADOW (wick), take a moving average of the
top-vs-bottom wick asymmetry, and ask: does that "market formation" separate a
WINNING fade (the extreme is being REJECTED -> price reverses) from the -20p
breakout-fade LOSER (the extreme is NOT rejected -> price breaks through)?

This is the exact failure mode of the lead's SELL fades. The prior per-candle
rejection-wick gate was FALSIFIED (rejection-wick-falsified memory); this is the
DIFFERENT day-aggregate moving-average-of-wick-area version.

Data: eurusd_m15_may2026.csv (actually 2026-06-08 .. 07-02 M15). Single regime,
so this is a CONCEPT test, not validation. Pure calc; writes nothing.

    python -m research.wick_formation.measure_wick_formation
"""
from __future__ import annotations
import csv, os, statistics

PIP = 0.0001
LB, W, BR, HZ = 20, 8, 20.0, 48   # range lookback, wick-MA window, bracket pips, horizon bars
_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "eurusd_m15_may2026.csv")


def _load():
    O = H = L = C = None
    rows = []
    with open(_CSV) as f:
        for r in csv.DictReader(f):
            try:
                rows.append((float(r["Open"]), float(r["High"]),
                             float(r["Low"]), float(r["Close"])))
            except Exception:
                continue
    return rows


def _ma(a, i, w):
    s = a[max(0, i - w + 1):i + 1]
    return sum(s) / len(s) if s else 0.0


def _auc(pairs):
    w = [f for f, y in pairs if y == 1]
    l = [f for f, y in pairs if y == 0]
    if not w or not l:
        return None
    g = t = 0
    for a in w:
        for b in l:
            t += 1
            g += 1 if a > b else (0.5 if a == b else 0)
    return g / t


def build_setups(rows):
    n = len(rows)
    O = [x[0] for x in rows]; H = [x[1] for x in rows]
    L = [x[2] for x in rows]; C = [x[3] for x in rows]
    up = [(H[i] - max(O[i], C[i])) / PIP for i in range(n)]
    lo = [(min(O[i], C[i]) - L[i]) / PIP for i in range(n)]

    def bracket(i, sell):
        e = C[i]
        for j in range(i + 1, min(n, i + 1 + HZ)):
            if sell:
                if (H[j] - e) / PIP >= BR: return "LOSS"   # ran up past stop
                if (e - L[j]) / PIP >= BR: return "WIN"    # fell to TP
            else:
                if (e - L[j]) / PIP >= BR: return "LOSS"
                if (H[j] - e) / PIP >= BR: return "WIN"
        return None

    sell, buy = [], []
    for i in range(LB, n - 1):
        hi = max(H[i - LB:i + 1]); lw = min(L[i - LB:i + 1]); rng = hi - lw
        if rng <= 0:
            continue
        pos = (C[i] - lw) / rng
        if pos >= 0.80:
            out = bracket(i, True)
            if out:
                sell.append((i, _ma(up, i, W) - _ma(lo, i, W),
                             1 if out == "WIN" else 0))   # feat>0 = top rejection
        elif pos <= 0.20:
            out = bracket(i, False)
            if out:
                buy.append((i, _ma(lo, i, W) - _ma(up, i, W),
                            1 if out == "WIN" else 0))
    return sell, buy


def _bootstrap_ci(S, iters=300):
    seed = 1
    def rnd():
        nonlocal seed; seed = (seed * 16807) % 2147483647; return seed / 2147483647
    aucs = []
    for _ in range(iters):
        samp = [S[int(rnd() * len(S))] for _ in range(len(S))]
        a = _auc([(f, y) for _, f, y in samp])
        if a is not None:
            aucs.append(a)
    aucs.sort()
    return aucs[int(0.025 * len(aucs))], aucs[int(0.975 * len(aucs))]


def _walk_forward(S):
    S = sorted(S, key=lambda x: x[0]); mid = len(S) // 2
    tr, te = S[:mid], S[mid:]
    thr = statistics.median(f for _, f, _ in tr)
    keep = [y for _, f, y in te if f >= thr]
    skip = [y for _, f, y in te if f < thr]
    wr = lambda x: (sum(x) / len(x)) if x else float("nan")
    base = sum(y for _, _, y in te) / len(te)
    return thr, base, (wr(keep), len(keep)), (wr(skip), len(skip))


def main():
    rows = _load()
    sell, buy = build_setups(rows)
    print(f"bars={len(rows)}  SELL setups={len(sell)}  BUY setups={len(buy)}")
    for name, S in (("SELL", sell), ("BUY", buy)):
        a = _auc([(f, y) for _, f, y in S])
        wr = sum(y for _, _, y in S) / len(S)
        print(f"\n{name}: winrate={wr:.0%}  AUC(rejection->WIN)={a:.3f}  "
              f"{'SEPARATES' if abs(a-0.5)>=0.10 else 'no separation'}")
        if name == "SELL":
            lo_ci, hi_ci = _bootstrap_ci(S)
            print(f"  bootstrap 95% CI [{lo_ci:.3f}, {hi_ci:.3f}] excludes 0.5: {lo_ci>0.5}")
            thr, base, (kw, kn), (sw, sn) = _walk_forward(S)
            print(f"  walk-forward (train->test by time): baseline={base:.0%}")
            print(f"    KEEP rejection fades: {kw:.0%} (n={kn})   "
                  f"SKIP breakout fades: {sw:.0%} (n={sn})")
    print("\nCAVEAT: single Jun-Jul regime + proxy setups. Concept test, NOT validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
