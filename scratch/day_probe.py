import os, sys, math, time, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_patterns as bp

SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "XAUUSD"]


def kurt(xs):
    n = len(xs)
    if n < 4: return None
    m = sum(xs)/n; v = sum((x-m)**2 for x in xs)/n
    if v <= 0: return None
    return sum((x-m)**4 for x in xs)/n/(v*v)


res = {}
for sym in SYMS:
    st = bp.LoadStats()
    t = bp.load_ticks(sym, st)
    pip = bp.pip_size(sym); pt = pip/10.0
    byday = defaultdict(list)
    for ep, px in t:
        byday[time.strftime("%Y-%m-%d", time.gmtime(ep))].append((ep, px))
    rows = []
    for d in sorted(byday):
        s = byday[d]
        if len(s) < 200: continue
        gaps = [s[i][0]-s[i-1][0] for i in range(1, len(s))]
        steps = [abs(s[i][1]-s[i-1][1])/pt for i in range(1, len(s))]
        r = [math.log(s[i][1]/s[i-1][1]) for i in range(1, len(s))]
        # within-day sqrt(dt): short (<=2s) vs long (10-30s)
        sh = [steps[i] for i in range(len(gaps)) if 0 < gaps[i] <= 2]
        lg = [steps[i] for i in range(len(gaps)) if 10 < gaps[i] <= 30]
        hist = defaultdict(int)
        for x in steps: hist[round(x)] += 1
        top = sorted(hist.items(), key=lambda kv: -kv[1])[:5]
        rows.append({
            "day": d, "n": len(s),
            "ticks_per_min": round(60*len(s)/max(1.0, s[-1][0]-s[0][0]), 1),
            "gap_med": round(sorted(gaps)[len(gaps)//2], 3),
            "gap_mean": round(sum(gaps)/len(gaps), 3),
            "frac_gap_1s": round(sum(1 for g in gaps if 0.9 <= g <= 1.1)/len(gaps), 3),
            "mean_step_pts": round(sum(steps)/len(steps), 3),
            "frac_zero": round(sum(1 for x in steps if x < 0.5)/len(steps), 3),
            "kurt": None if kurt(r) is None else round(kurt(r), 1),
            "short_step": round(sum(sh)/len(sh), 3) if sh else None,
            "long_step": round(sum(lg)/len(lg), 3) if lg else None,
            "n_long": len(lg),
            "top_steps": top,
            "px_lo": round(min(x[1] for x in s), 5),
            "px_hi": round(max(x[1] for x in s), 5),
        })
    res[sym] = rows

print(json.dumps(res, indent=0))
