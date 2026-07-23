import os, sys, math, time, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_patterns as bp

SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "XAUUSD"]
HOR = [1, 5, 15, 60, 300, 900]

for sym in SYMS:
    st = bp.LoadStats()
    t = bp.load_ticks(sym, st)
    pip = bp.pip_size(sym); pt = pip / 10.0
    # last price on each 1s grid point, within continuous stretches only
    grid = {}
    for ep, px in t:
        grid[int(ep)] = px
    keys = sorted(grid)
    print("==", sym, "grid_secs=%d" % len(keys))
    for h in HOR:
        d = []
        for k in keys:
            k2 = k + h
            if k2 in grid:
                d.append((grid[k2] - grid[k]) / pt)
        if len(d) < 200:
            print("  h=%-4d n=%-7d (thin)" % (h, len(d))); continue
        m = sum(d) / len(d)
        v = sum((x - m) ** 2 for x in d) / len(d)
        m4 = sum((x - m) ** 4 for x in d) / len(d)
        print("  h=%-4ds n=%-7d sd_pts=%-9.3f var/dt=%-10.3f kurt=%-9.2f" % (
            h, len(d), math.sqrt(v), v / h, (m4 / (v * v)) if v > 0 else 0))
    # small-step depletion check
    steps = [abs(t[i][1] - t[i-1][1]) / pt for i in range(1, len(t))]
    hist = defaultdict(int)
    for x in steps:
        b = int(round(x))
        hist[b if b <= 200 else 999] += 1
    n = len(steps)
    lo = [(b, round(hist.get(b, 0)/n, 5)) for b in range(0, 11)]
    print("  step_hist_0_10_pts:", lo)
    print("  frac_step_gt_20pts:", round(sum(1 for x in steps if x > 20)/n, 5))
