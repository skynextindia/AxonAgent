"""READ-ONLY microstructure sanity gate on REAL weekday ticks.

Reuses backtest_patterns.load_ticks / build_bars verbatim. No engine touched.
"""
import os
import sys
import math
import json
import time
import datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_patterns as bp

SYMBOLS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "XAUUSD"]
MIN_TICKS = 5
MAX_GAP_S = 3600
DROP_EDGE = 1
MIN_SEG_BARS = 20


def kurt(xs):
    n = len(xs)
    if n < 4:
        return None
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / n
    if v <= 0:
        return None
    m4 = sum((x - m) ** 4 for x in xs) / n
    return m4 / (v * v)


def ac1(xs):
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, n))
    den = sum((x - m) ** 2 for x in xs)
    return num / den if den > 0 else None


def pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da > 0 and db > 0 else None


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(q * (len(s) - 1))))
    return s[i]


out = {}
for sym in SYMBOLS:
    st = bp.LoadStats()
    ticks = bp.load_ticks(sym, st)
    pip = bp.pip_size(sym)
    point = pip / 10.0  # 5-digit / 3-digit point
    rec = {
        "rows_read": st.rows_read,
        "dropped_synth_epoch": st.rows_synthetic_epoch,
        "dropped_synth_signature": st.rows_synthetic_signature,
        "ticks_kept": st.ticks_kept,
    }
    if len(ticks) < 100:
        out[sym] = rec
        continue
    rec["span"] = [
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ticks[0][0])),
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ticks[-1][0])),
    ]

    # ---- 1. per-day tick-return kurtosis (log returns) ----
    byday = defaultdict(list)
    for ep, px in ticks:
        byday[time.strftime("%Y-%m-%d", time.gmtime(ep))].append((ep, px))
    daily = []
    for d in sorted(byday):
        seq = byday[d]
        if len(seq) < 200:
            continue
        r = []
        for i in range(1, len(seq)):
            p0, p1 = seq[i - 1][1], seq[i][1]
            if p0 > 0 and p1 > 0:
                r.append(math.log(p1 / p0))
        k = kurt(r)
        daily.append({
            "day": d,
            "dow": dt.datetime.strptime(d, "%Y-%m-%d").strftime("%a"),
            "n": len(r),
            "kurtosis": None if k is None else round(k, 3),
            "ac1": None if ac1(r) is None else round(ac1(r), 4),
        })
    rec["daily"] = daily
    ks = [d["kurtosis"] for d in daily if d["kurtosis"] is not None]
    rec["kurtosis_median"] = round(sorted(ks)[len(ks) // 2], 3) if ks else None
    rec["kurtosis_min"] = round(min(ks), 3) if ks else None
    rec["kurtosis_max"] = round(max(ks), 3) if ks else None

    # ---- pooled returns / steps ----
    allr = []
    steps = []   # (dt, abs step in pips)
    for i in range(1, len(ticks)):
        d_t = ticks[i][0] - ticks[i - 1][0]
        if d_t <= 0 or d_t > 300:
            continue
        p0, p1 = ticks[i - 1][1], ticks[i][1]
        allr.append(math.log(p1 / p0))
        steps.append((d_t, abs(p1 - p0) / pip))
    rec["ac1_pooled"] = None if ac1(allr) is None else round(ac1(allr), 4)
    rec["kurtosis_pooled"] = None if kurt(allr) is None else round(kurt(allr), 3)

    # ---- 2. sqrt(dt) scaling ----
    buckets = [("<=2s", 0.0, 2.0), ("2-10s", 2.0, 10.0),
               ("10-30s", 10.0, 30.0), ("30-120s", 30.0, 120.0)]
    scal = []
    for name, lo, hi in buckets:
        sel = [s for (d_t, s) in steps if lo < d_t <= hi] if lo > 0 else \
              [s for (d_t, s) in steps if 0 < d_t <= hi]
        dts = [d_t for (d_t, s) in steps if (lo < d_t <= hi if lo > 0 else 0 < d_t <= hi)]
        if not sel:
            scal.append({"bucket": name, "n": 0})
            continue
        mdt = sum(dts) / len(dts)
        scal.append({
            "bucket": name, "n": len(sel),
            "mean_dt_s": round(mdt, 3),
            "mean_abs_step_pips": round(sum(sel) / len(sel), 4),
            "per_sqrt_dt": round((sum(sel) / len(sel)) / math.sqrt(mdt), 4),
        })
    rec["dt_scaling"] = scal
    ok = [b for b in scal if b.get("n", 0) >= 50]
    if len(ok) >= 2:
        r0 = ok[0]["mean_abs_step_pips"]
        rn = ok[-1]["mean_abs_step_pips"]
        pred = r0 * math.sqrt(ok[-1]["mean_dt_s"] / ok[0]["mean_dt_s"])
        rec["scaling_observed_ratio"] = round(rn / r0, 3)
        rec["scaling_sqrt_predicted_ratio"] = round(pred / r0, 3)

    # ---- 3. corr(log bar range, log tick count) on M15 ----
    bars, diag = bp.build_bars(ticks, MIN_TICKS, MAX_GAP_S, DROP_EDGE, MIN_SEG_BARS)
    rec["m15"] = dict(diag)
    lr, lc = [], []
    for b in bars:
        rng = b.h - b.l
        if rng > 0 and b.ticks > 0:
            lr.append(math.log(rng))
            lc.append(math.log(b.ticks))
    rec["corr_logrange_logticks_m15"] = None if pearson(lr, lc) is None else round(pearson(lr, lc), 4)
    rec["m15_bar_ticks_median"] = pct([b.ticks for b in bars], 0.5)

    # ---- 5. granularity ----
    diffs = [abs(ticks[i][1] - ticks[i - 1][1]) for i in range(1, len(ticks))]
    nz = [d for d in diffs if d > 1e-12]
    rec["frac_zero_step"] = round(1.0 - len(nz) / float(len(diffs)), 4)
    if nz:
        rec["min_nonzero_step_points"] = round(min(nz) / point, 4)
        rec["median_step_points"] = round(pct(nz, 0.5) / point, 4)
        # quantization: how many distinct step sizes, and are they integer points
        q = defaultdict(int)
        for d in nz:
            q[round(d / point, 2)] += 1
        top = sorted(q.items(), key=lambda kv: -kv[1])[:8]
        rec["top_step_sizes_points"] = [[k, v] for k, v in top]
        integral = sum(v for k, v in q.items() if abs(k - round(k)) < 0.02)
        rec["frac_steps_integer_points"] = round(integral / float(len(nz)), 4)
        rec["distinct_step_sizes"] = len(q)

    # ---- inter-tick gap distribution ----
    gaps = [ticks[i][0] - ticks[i - 1][0] for i in range(1, len(ticks))]
    rec["gap_median_s"] = round(pct(gaps, 0.5), 3)
    rec["gap_p90_s"] = round(pct(gaps, 0.90), 3)
    rec["gap_p99_s"] = round(pct(gaps, 0.99), 3)
    rec["ticks_per_min_mean"] = round(60.0 * len(ticks) / max(1.0, ticks[-1][0] - ticks[0][0]), 3)

    # ---- M5 viability ----
    old = bp.BAR_SECONDS
    try:
        bp.BAR_SECONDS = 300
        b5, d5 = bp.build_bars(ticks, MIN_TICKS, MAX_GAP_S, DROP_EDGE, MIN_SEG_BARS)
        rec["m5"] = dict(d5)
        rec["m5_bar_ticks_median"] = pct([b.ticks for b in b5], 0.5)
        rec["m5_frac_zero_range"] = round(
            sum(1 for b in b5 if b.h - b.l <= 1e-12) / float(max(1, len(b5))), 4)
        rec["m5_median_range_pips"] = round(
            pct([(b.h - b.l) / pip for b in b5], 0.5) or 0.0, 3)
        # raw M5 buckets before min_ticks
        agg = set()
        for ep, px in ticks:
            agg.add((int(ep) // 300) * 300)
        rec["m5_raw_buckets"] = len(agg)
    finally:
        bp.BAR_SECONDS = old
    rec["m15_median_range_pips"] = round(pct([(b.h - b.l) / pip for b in bars], 0.5) or 0.0, 3)

    out[sym] = rec

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "microstructure_gate.json")
with open(p, "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1)
print(json.dumps(out, indent=1))
