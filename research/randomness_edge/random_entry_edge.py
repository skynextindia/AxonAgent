"""Can RANDOM (coin-flip) decisions make an edge? And where is the 'randomness factor'?

Theory: for a driftless random walk, the optional-stopping theorem says ANY bounded
bracket has E[gross]=0, so random-entry E[net] = -cost < 0. An edge from random
entries is possible ONLY if the real price path deviates from a martingale — i.e.
if some exit structure (fat-tail TP, trailing stop) captures real drift/trend, or
if the 'randomness factor' (variance ratio / Hurst) departs from 0.5 at some horizon.

This script, on REAL H1 EURUSD (2y):
  A) Monte-Carlo RANDOM entries (coin-flip long/short, one position at a time),
     under several exits, reporting GROSS (cost=0) and NET across many seeds.
     If GROSS ~ 0 -> martingale -> no random edge exists (only cost matters).
  B) VARIANCE RATIO by horizon (the randomness factor). VR>1 trending (momentum
     edge), VR<1 mean-reverting (fade edge), VR=1 pure random (no edge).

    python -m research.randomness_edge.random_entry_edge
"""
from __future__ import annotations
import csv, os, random
from statistics import mean, pstdev

PIP = 0.0001
COST = 2.0
MAX_HOLD = 120
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")


def load_h1():
    bars = []
    with open(os.path.join(_ROOT, "eurusd_h1_2y.csv")) as f:
        for r in csv.DictReader(f):
            try:
                bars.append((float(r["High"]), float(r["Low"]), float(r["Close"])))
            except Exception:
                continue
    return bars


def atr_array(bars, n=14):
    """Precompute ATR(n) for every bar once (O(N)). Returns list aligned to bars."""
    tr = [12.0 * PIP] * len(bars)
    for k in range(1, len(bars)):
        h, l, _ = bars[k]
        pc = bars[k - 1][2]
        tr[k] = max(h - l, abs(h - pc), abs(l - pc))
    out = [12.0 * PIP] * len(bars)
    run = sum(tr[1:n + 1])
    for i in range(len(bars)):
        if i >= n:
            run += tr[i] - tr[i - n]
            out[i] = run / n
    return out


def run_random(bars, seed, atrs, sl_p=None, tp_p=None, trail_atr=None, cost=0.0):
    """One seed: coin-flip entries, one position at a time. Returns list of net pips.
    Exit = fixed bracket (sl_p,tp_p) OR ATR-trailing (trail_atr) letting winners run."""
    rng = random.Random(seed)
    out = []
    n = len(bars)
    i = 20
    while i < n - 1:
        long = rng.random() < 0.5
        entry = bars[i][2]
        sl = (sl_p or 20) * PIP
        best = entry
        trail_stop = entry - sl if long else entry + sl
        outcome = None
        j = i + 1
        while j < min(i + 1 + MAX_HOLD, n):
            h, l, c = bars[j]
            if trail_atr is not None:
                # update trailing stop behind the best excursion
                if long:
                    best = max(best, h)
                    trail_stop = max(trail_stop, best - trail_atr * atrs[j])
                    if l <= trail_stop:
                        outcome = (trail_stop - entry) / PIP; break
                else:
                    best = min(best, l)
                    trail_stop = min(trail_stop, best + trail_atr * atrs[j])
                    if h >= trail_stop:
                        outcome = (entry - trail_stop) / PIP; break
            else:
                tp = tp_p * PIP
                if long:
                    hit_sl = l <= entry - sl; hit_tp = h >= entry + tp
                else:
                    hit_sl = h >= entry + sl; hit_tp = l <= entry - tp
                if hit_sl and hit_tp:
                    outcome = -sl_p; break        # conservative
                if hit_tp:
                    outcome = tp_p; break
                if hit_sl:
                    outcome = -sl_p; break
            j += 1
        if outcome is None:
            lastc = bars[min(j, n - 1)][2]
            outcome = ((lastc - entry) if long else (entry - lastc)) / PIP
        out.append(outcome - cost)
        i = j + 1                                 # next entry after this exit (non-overlap)
    return out


def summarize(name, per_seed_means):
    m = mean(per_seed_means)
    sd = pstdev(per_seed_means)
    posp = 100.0 * sum(1 for x in per_seed_means if x > 0) / len(per_seed_means)
    print(f"  {name:34} mean/trade {m:+6.3f}p   across-seed sd {sd:.3f}   seeds net+ {posp:4.0f}%", flush=True)


def variance_ratio(bars, q):
    """VR(q): var of q-bar log returns / (q * var of 1-bar). 1=random, >1 trend, <1 revert."""
    import math
    px = [c for _, _, c in bars]
    r1 = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    v1 = pstdev(r1) ** 2
    rq = [math.log(px[i] / px[i - q]) for i in range(q, len(px))]
    vq = pstdev(rq) ** 2
    return vq / (q * v1) if v1 > 0 else float("nan")


def main() -> int:
    bars = load_h1()
    atrs = atr_array(bars)
    print(f"H1 bars {len(bars)}  cost={COST}p  seeds=150\n", flush=True)
    SEEDS = range(150)

    print("== A) RANDOM (coin-flip) ENTRIES — GROSS (cost=0): is there any free edge? ==")
    configs = [
        ("symmetric SL20/TP20", dict(sl_p=20, tp_p=20)),
        ("wide     SL20/TP100", dict(sl_p=20, tp_p=100)),
        ("inverse  SL100/TP20", dict(sl_p=100, tp_p=20)),
        ("trailing 2.0xATR (run winners)", dict(trail_atr=2.0)),
        ("trailing 4.0xATR (run winners)", dict(trail_atr=4.0)),
    ]
    gross = {}
    for name, kw in configs:
        means = [mean(run_random(bars, s, atrs, cost=0.0, **kw)) for s in SEEDS]
        gross[name] = means
        summarize(name, means)

    print("\n== A') SAME, NET of 2p cost (what you'd actually bank) ==")
    for name, kw in configs:
        means = [mean(run_random(bars, s, atrs, cost=COST, **kw)) for s in SEEDS]
        summarize(name, means)

    print("\n== B) RANDOMNESS FACTOR: variance ratio by horizon (1=pure random) ==")
    print("   VR>1.05 => momentum edge exists at that horizon; VR<0.95 => fade edge; else none")
    for q in (2, 4, 8, 12, 24, 48, 120):
        vr = variance_ratio(bars, q)
        tag = "TREND" if vr > 1.05 else "REVERT" if vr < 0.95 else "random"
        print(f"   horizon {q:3d}h : VR={vr:.3f}  [{tag}]")

    print("\nINTERPRETATION: if GROSS ~ 0.00 for every exit and VR ~ 1.00 at every horizon,")
    print("the market is a martingale at these scales -> NO random decision can make an edge;")
    print("random entry just pays the cost. Any edge must come from a NON-random signal")
    print("(and even the fade only clears cost on the wide bracket — see [[expectancy-verdict]]).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
