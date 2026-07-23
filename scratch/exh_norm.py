"""Same detector, but the hit bar is normalised to a common %% of ADR20 for every
pair instead of the per-pair reversal_median_pips. If 83%% vs 0-15%% is a real
pattern property it should survive; if it is a threshold artifact it collapses."""
import os, glob, csv, calendar, time, json, bisect, random, statistics
REPO = "D:/AXON.AI/AxonAgent-Agy"
exec(open(os.path.join(REPO, "scratch", "exh_audit.py")).read().split("def run(")[0])

FRAC = 0.05          # hit bar = 5% of ADR20 for every pair
for symbol in ["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY"]:
    pip = 0.01 if ("JPY" in symbol.upper() or "XAU" in symbol.upper()) else 0.0001
    T, P, D, R, per_file, seen = load(symbol)
    n = len(T)
    rs = json.load(open(os.path.join(REPO, "reports", f"range_stats_{symbol}.json")))
    adr = float(rs.get("adr20") or 0)
    exp = FRAC * adr                       # <-- normalised, not median-reversal
    RALLY = 0.5 * exp * pip
    PTH, DTH, DHI = 0.62, 0.10, 0.18
    last_t = -1e18; hits = 0; tot = 0; acts = []
    for i in range(n):
        if D[i] > DTH or R[i] < PTH:
            continue
        lo = bisect.bisect_left(T, T[i] - 900, 0, i)
        if lo >= i or max(D[lo:i + 1]) < DHI:
            continue
        move = P[i] - P[lo]
        if abs(move) < RALLY or T[i] - last_t < 1800:
            continue
        last_t = T[i]
        down = move > 0
        hj = bisect.bisect_right(T, T[i] + 3600, i, n)
        seg = list(range(i, hj)) or [i]
        a = ((P[i] - P[min(seg, key=lambda k: P[k])]) if down
             else (P[max(seg, key=lambda k: P[k])] - P[i])) / pip
        acts.append(a); hits += (a >= exp); tot += 1
    # null baseline at the same normalised bar
    random.seed(7)
    idxs = random.sample(range(n), min(4000, n)); nb = 0
    for i in idxs:
        hj = bisect.bisect_right(T, T[i] + 3600, i, n)
        seg = list(range(i, hj)) or [i]
        lo = bisect.bisect_left(T, T[i] - 900, 0, i)
        down = (P[i] - P[lo]) > 0 if lo < i else True
        a = ((P[i] - P[min(seg, key=lambda k: P[k])]) if down
             else (P[max(seg, key=lambda k: P[k])] - P[i])) / pip
        nb += (a >= exp)
    print(f"{symbol:7s} adr20={adr:8.1f}p  bar={exp:7.1f}p ({FRAC*100:.0f}% ADR)  "
          f"signals={tot:3d}  HIT={100.0*hits/max(1,tot):5.1f}%  "
          f"null={100.0*nb/len(idxs):5.1f}%  avgMFE={statistics.mean(acts) if acts else 0:7.1f}p "
          f"(={100*statistics.mean(acts)/adr if acts else 0:4.1f}% ADR)")
