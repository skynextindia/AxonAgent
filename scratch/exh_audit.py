"""READ-ONLY replication of /api/exhaustion (api_server.py L758-835) + null baseline.
Does not import or touch axonai/. Nothing is written back into the engine."""
import os, glob, csv, calendar, time, json, bisect, random, statistics

REPO = "D:/AXON.AI/AxonAgent-Agy"


def load(symbol):
    paths = glob.glob(os.path.join(REPO, "reports", f"engine_snapshots_{symbol}*.csv"))
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    T = []; P = []; D = []; R = []; seen = 0
    per_file = []
    for fp in paths:
        c0 = seen
        with open(fp, "r", encoding="utf-8", errors="ignore", newline="") as f:
            for r in csv.DictReader(f):
                ts = (r.get("timestamp") or "")[:19]
                if len(ts) < 19:
                    continue
                try:
                    ep = calendar.timegm(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                    p = float(r.get("price") or 0)
                    d = float(r.get("disp_ratio") or 0)
                    rv = float(r.get("reversal_pressure") or 0)
                except (ValueError, TypeError):
                    continue
                if p <= 0:
                    continue
                T.append(ep); P.append(p); D.append(d); R.append(rv); seen += 1
        per_file.append((os.path.basename(fp), seen - c0))
        if seen > 250000:
            break
    z = sorted(range(len(T)), key=lambda k: T[k])
    return ([T[k] for k in z], [P[k] for k in z], [D[k] for k in z], [R[k] for k in z],
            per_file, seen)


def run(symbol):
    pip = 0.01 if ("JPY" in symbol.upper() or "XAU" in symbol.upper()) else 0.0001
    T, P, D, R, per_file, seen = load(symbol)
    n = len(T)
    if n < 200:
        print(f"{symbol}: too few rows ({n})"); return
    rs = json.load(open(os.path.join(REPO, "reports", f"range_stats_{symbol}.json")))
    exp = float(rs.get("reversal_median_pips") or 0)
    if exp <= 0:
        exp = 200.0 if "XAU" in symbol.upper() else 8.0
    RALLY = 0.5 * exp * pip
    PTH, DTH, DHI = 0.62, 0.10, 0.18
    adr = rs.get("adr20") or 0

    # duplicate-timestamp / overlap diagnostics
    dup = sum(1 for i in range(1, n) if T[i] == T[i - 1])
    gaps = [T[i] - T[i - 1] for i in range(1, n)]
    big_gaps = sum(1 for g in gaps if g > 900)

    last_t = -1e18; pats = []
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
        if down:
            ex = min(seg, key=lambda k: P[k]); act = (P[i] - P[ex]) / pip
        else:
            ex = max(seg, key=lambda k: P[k]); act = (P[ex] - P[i]) / pip
        # adverse excursion over the same window (never checked by the endpoint)
        if down:
            adv = (max(seg, key=lambda k: P[k]) and (P[max(seg, key=lambda k: P[k])] - P[i]) / pip)
        else:
            adv = (P[i] - P[min(seg, key=lambda k: P[k])]) / pip
        pats.append({"i": i, "t": T[i], "act": act, "adv": adv,
                     "hit": bool(act >= exp),
                     "fwd_span": T[seg[-1]] - T[i], "lookback_span": T[i] - T[lo],
                     "n_fwd": len(seg)})

    def rate(rows):
        return (100.0 * sum(r["hit"] for r in rows) / len(rows)) if rows else 0.0

    # NULL BASELINE: same outcome rule at random ticks, direction assigned by
    # coin flip and by the same "move" sign rule, no pattern condition at all.
    random.seed(7)
    idxs = random.sample(range(n), min(4000, n))
    nb_hits = 0; nb_acts = []
    for i in idxs:
        hj = bisect.bisect_right(T, T[i] + 3600, i, n)
        seg = list(range(i, hj)) or [i]
        lo = bisect.bisect_left(T, T[i] - 900, 0, i)
        down = (P[i] - P[lo]) > 0 if lo < i else True
        if down:
            a = (P[i] - P[min(seg, key=lambda k: P[k])]) / pip
        else:
            a = (P[max(seg, key=lambda k: P[k])] - P[i]) / pip
        nb_acts.append(a); nb_hits += (a >= exp)
    nb_rate = 100.0 * nb_hits / len(idxs)

    # "either direction" baseline: max 1h excursion in EITHER direction >= exp
    both = 0
    for i in idxs:
        hj = bisect.bisect_right(T, T[i] + 3600, i, n)
        seg = list(range(i, hj)) or [i]
        up = (P[max(seg, key=lambda k: P[k])] - P[i]) / pip
        dn = (P[i] - P[min(seg, key=lambda k: P[k])]) / pip
        both += (max(up, dn) >= exp)

    print(f"\n===== {symbol} =====")
    print(f" rows={n} files={per_file} dup_ts={dup} gaps>900s={big_gaps}")
    print(f" data span: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(T[0]))} -> "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(T[-1]))}")
    print(f" exp(reversal_median_pips)={exp}  RALLY={RALLY/pip:.1f}p  adr20={adr}p")
    print(f" exp as %% of ADR20 = {100.0*exp/adr:.2f}%   RALLY as %% ADR = {100.0*(exp/2)/adr:.2f}%")
    print(f" SIGNALS: all={len(pats)} hit_rate_ALL={rate(pats):.1f}%  "
          f"hit_rate_LAST15={rate(pats[-15:]):.1f}% (n={len(pats[-15:])})")
    if pats:
        print(f"  avg act_pips (MFE) all={statistics.mean(p['act'] for p in pats):.1f}  "
              f"last15={statistics.mean(p['act'] for p in pats[-15:]):.1f}")
        print(f"  avg ADVERSE excursion same window = "
              f"{statistics.mean(p['adv'] for p in pats):.1f}p  "
              f"(MFE>=adverse on {100.0*sum(1 for p in pats if p['act']>=p['adv'])/len(pats):.0f}% of signals)")
        trunc = sum(1 for p in pats if p["fwd_span"] < 3500)
        print(f"  signals with TRUNCATED forward window (<3500s of data) = {trunc}")
        stale = sum(1 for p in pats if p["lookback_span"] > 1200)
        print(f"  signals whose 900s lookback actually spans >1200s (gap) = {stale}")
    print(f" NULL BASELINE (random ticks, same rule): hit={nb_rate:.1f}%  "
          f"avg MFE={statistics.mean(nb_acts):.1f}p")
    print(f" NULL 'either direction' 1h excursion >= exp: {100.0*both/len(idxs):.1f}%")


for s in ["XAUUSD", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY"]:
    try:
        run(s)
    except Exception as e:
        print(s, "ERR", type(e).__name__, e)
