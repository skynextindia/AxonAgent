#!/usr/bin/env python3
"""EXHAUSTION-GRADING PROBE (read-only reconstruction; never trades).

Question: if we graded each fade by how 'exhausted' the move was (the proposed 5th
regime state = wide-range + low-ER at an extreme), would that SPLIT realized P&L?

Method (trustworthy reconstruction, not a strategy sim):
  1. Join every fired+executed entry (peak_detection row, trade_result.order) to its
     realized pips (trade_closed.ticket).  n~301.
  2. At each entry, from REAL M15 bars STRICTLY BEFORE trigger_candle.open_time,
     recompute the 5th-state ingredients: Kaufman ER (20 & 60), range width in ATR,
     and 'extremeness' (how far into the range box the entry fired). Plus the already
     logged microstructure reads (velocity_divergence, price_per_tick_efficiency).
  3. Bucket by each feature (tertiles) + a composite 'exhaustion' score; report
     net/mean pips + win% per bucket and the top-vs-bottom gap.
  4. SHUFFLE-NULL every headline: permute pips across entries N=20000 and see if the
     real Spearman corr / tertile gap sits outside the random band (two-sided p).
Combined + per symbol. Verdict = does exhaustion depth beat the null?"""
import json
from pathlib import Path
import numpy as np

SCRATCH = Path(r"C:\Users\User\AppData\Local\Temp\claude\F--AxonAi-main\946c1cf8-7386-4149-986e-ed301bd7e0f9\scratchpad")
LOG = Path(r"F:\AxonAi_main\reports\signals.jsonl")
RNG = np.random.default_rng(20260816)
NSHUF = 20000


def canon(s):
    s = (s or "").upper().replace(".I", "").replace(".", "").strip()
    return s[:6]


# ---- 1. load + join ----
fires, closes = [], {}
for ln in open(LOG, encoding="utf-8"):
    ln = ln.strip()
    if not ln:
        continue
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if d.get("event_type") == "peak_detection" and isinstance(d.get("trade_result"), dict):
        tr = d["trade_result"]
        if tr.get("retcode") == 10009 and tr.get("order"):
            fires.append(d)
    elif d.get("type") == "trade_closed" and d.get("ticket") is not None:
        closes[int(d["ticket"])] = d

entries = []
for d in fires:
    c = closes.get(int(d["trade_result"]["order"]))
    if not c:
        continue
    ed = d.get("event_details") or {}
    tc = ed.get("trigger_candle") or {}
    if tc.get("open_time") is None:
        continue
    entries.append(dict(
        sym=canon(d.get("mt5_symbol")),
        direction=("Buy" if str(d.get("decision", "")).lower().startswith("b") else "Sell"),
        open_time=int(tc["open_time"]),
        peak_price=float(ed.get("peak_price") or tc.get("close") or 0.0),
        vdiv=float(ed.get("velocity_divergence") or 0.0),
        ppte=float(ed.get("price_per_tick_efficiency") or 0.0),
        pips=float(c["pips"]),
    ))
print(f"joined entries: {len(entries)}  (EURUSD {sum(e['sym']=='EURUSD' for e in entries)} / USDJPY {sum(e['sym']=='USDJPY' for e in entries)})")

# ---- 2. reconstruct M15 exhaustion features ----
bars = {}
for sym in ("EURUSD", "USDJPY"):
    z = np.load(SCRATCH / f"realtick90_{sym}.npz")
    bars[sym] = (np.asarray(z["b_time"], np.int64), np.asarray(z["b_open"], float),
                 np.asarray(z["b_high"], float), np.asarray(z["b_low"], float), np.asarray(z["b_close"], float))


def er(cl):
    net = abs(cl[-1] - cl[0]); path = np.sum(np.abs(np.diff(cl)))
    return float(net / path) if path > 0 else 0.0


covered = grid_ok = 0
for e in entries:
    bt, bo, bh, bl, bc = bars[e["sym"]]
    j = int(np.searchsorted(bt, e["open_time"], side="right"))  # bars with time <= open_time -> [ :j ]
    if j < 20:
        e["ok"] = False
        continue
    if abs(int(bt[j - 1]) - e["open_time"]) <= 900:
        grid_ok += 1
    lb = 60
    lo = max(0, j - lb)
    cl, hi, low = bc[lo:j], bh[lo:j], bl[lo:j]
    if len(cl) < 20:
        e["ok"] = False
        continue
    rb_hi = hi[-20:].max(); rb_lo = low[-20:].min()
    span = (rb_hi - rb_lo) or 1e-9
    rng14 = (hi[-14:] - low[-14:]); atr = float(rng14.mean()) if len(rng14) else span
    price = e["peak_price"] or cl[-1]
    rp = (price - rb_lo) / span
    e["er20"] = er(cl[-20:]); e["er60"] = er(cl)
    e["width_atr"] = float(span / atr) if atr > 0 else 0.0
    e["extreme"] = float(abs(rp - 0.5) * 2.0)      # 0 = mid-range, 1 = at an edge
    e["ok"] = True
    covered += 1
print(f"reconstructed: {covered}/{len(entries)}  (trigger-bar grid-matched: {grid_ok})\n")

E = [e for e in entries if e.get("ok")]

# ---- stats helpers ----
def rankdata(a):
    a = np.asarray(a, float); order = a.argsort(); ranks = np.empty(len(a), float)
    ranks[order] = np.arange(1, len(a) + 1)
    # average ties
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt)); np.add.at(sums, inv, ranks)
    return (sums / cnt)[inv]


def pearson(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    a = a - a.mean(); b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def spearman(a, b):
    return pearson(rankdata(a), rankdata(b))


def shuf_p_corr(feat, pips, obs):
    feat = np.asarray(feat, float); rf = rankdata(feat)
    rp = rankdata(pips); cnt = 0
    for _ in range(NSHUF):
        if abs(pearson(rf, RNG.permutation(rp))) >= abs(obs) - 1e-12:
            cnt += 1
    return (cnt + 1) / (NSHUF + 1)


def tertile_gap(feat, pips, hi_is_exhausted):
    """mean-pip gap: most-exhausted tertile minus least-exhausted tertile."""
    feat = np.asarray(feat, float); pips = np.asarray(pips, float)
    q1, q2 = np.quantile(feat, [1 / 3, 2 / 3])
    lo = pips[feat <= q1]; hi = pips[feat >= q2]
    if hi_is_exhausted:
        return float(hi.mean() - lo.mean()), lo, hi
    return float(lo.mean() - hi.mean()), hi, lo


# feature dir: True => HIGHER value = MORE exhausted (fade thesis expects MORE pips)
FEATURES = [
    ("er20 (low=exhausted)", "er20", False),
    ("er60 (low=exhausted)", "er60", False),
    ("width_atr (wide=exhausted)", "width_atr", True),
    ("extreme (edge=exhausted)", "extreme", True),
    ("velocity_divergence (hi=exhausted)", "vdiv", True),
    ("ppte (low=exhausted)", "ppte", False),
]


def zc(x):
    x = np.asarray(x, float); s = x.std()
    return (x - x.mean()) / s if s > 0 else x * 0.0


def run(label, rows):
    if len(rows) < 24:
        print(f"### {label}: n={len(rows)} too small\n")
        return
    pips = np.array([r["pips"] for r in rows])
    w = (pips > 0).mean() * 100
    print(f"### {label}  (n={len(rows)}, win {w:.0f}%, net {pips.sum():+.1f}p, mean {pips.mean():+.2f}p)")
    print(f"{'feature':<34}{'spearman':>10}{'shuf-p':>9}   most-exh  least-exh   gap(p)   gap-p")
    # composite exhaustion score
    comp = zc([-r["er20"] for r in rows]) + zc([r["width_atr"] for r in rows]) + zc([r["extreme"] for r in rows])
    feats = FEATURES + [("COMPOSITE (low-ER+wide+edge)", None, True)]
    for name, key, hi_exh in feats:
        vals = comp if key is None else np.array([r[key] for r in rows])
        # orient so 'higher = more exhausted' for the corr sign we expect positive
        oriented = vals if hi_exh else -vals
        sp = spearman(oriented, pips)
        gap, exh_grp, cool_grp = tertile_gap(oriented, pips, True)
        p_sp = shuf_p_corr(oriented, pips, sp)
        # gap null
        rp = pips.copy(); cnt = 0; ov = np.asarray(oriented, float)
        q1, q2 = np.quantile(ov, [1 / 3, 2 / 3]); mask_hi = ov >= q2; mask_lo = ov <= q1
        for _ in range(NSHUF):
            s = RNG.permutation(rp)
            g = s[mask_hi].mean() - s[mask_lo].mean()
            if abs(g) >= abs(gap) - 1e-12:
                cnt += 1
        p_gap = (cnt + 1) / (NSHUF + 1)
        flag = "  <== " if (p_sp < 0.05 or p_gap < 0.05) else ""
        print(f"{name:<34}{sp:>+10.3f}{p_sp:>9.3f}   {exh_grp.mean():>+7.2f}  {cool_grp.mean():>+8.2f}  {gap:>+7.2f}  {p_gap:>6.3f}{flag}")
    print()


run("COMBINED", E)
run("EURUSD", [e for e in E if e["sym"] == "EURUSD"])
run("USDJPY", [e for e in E if e["sym"] == "USDJPY"])
print("NOTE: 7 features x 3 groups = 21 tests; at p<0.05 expect ~1 false positive by chance.")
print("A real, buildable signal = a feature that splits P&L with p<0.05 AND survives per-symbol AND points the fade-thesis way (more exhausted -> more pips).")
print("[DONE-MARKER]")
