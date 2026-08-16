#!/usr/bin/env python3
"""EXHAUSTION-GRADING PROBE — MFE TIEBREAKER (read-only; never trades).

Same question as exhaustion_grade_probe.py, but the target is MFE (max favorable
excursion) reconstructed from REAL TICKS instead of realized pips — so the exit
engine is fully removed and we measure PURE ENTRY QUALITY: after we entered, how
far did price actually go our way?

Per entry: find the fill tick (closest mid to trade_result.price within the entry
bar), then MFE = max favorable (mid vs fill) over the next H minutes, STRICTLY after
the fill tick (no look-ahead). Grade the same exhaustion features against MFE with a
20k-shuffle null, combined + per symbol. If exhaustion depth graded ENTRY quality,
it should show up here even if the tight exit masked it in realized pips."""
import json
from pathlib import Path
import numpy as np

SCRATCH = Path(r"C:\Users\User\AppData\Local\Temp\claude\F--AxonAi-main\946c1cf8-7386-4149-986e-ed301bd7e0f9\scratchpad")
LOG = Path(r"F:\AxonAi_main\reports\signals.jsonl")
RNG = np.random.default_rng(20260817)
NSHUF = 20000
H_MIN = 60           # forward MFE horizon (minutes)
FILL_TOL_PIPS = 3.0  # max |mid-fill| to accept a fill-tick match


def canon(s):
    s = (s or "").upper().replace(".I", "").replace(".", "").strip()
    return s[:6]


def pipsz(sym):
    return 0.01 if ("JPY" in sym or "XAU" in sym) else 0.0001


# ---- 1. load + join (+ fill price + direction) ----
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
    tr = d["trade_result"]
    if tc.get("open_time") is None or not tr.get("price"):
        continue
    entries.append(dict(
        sym=canon(d.get("mt5_symbol")),
        buy=str(d.get("decision", "")).lower().startswith("b"),
        open_time=int(tc["open_time"]),
        fill=float(tr["price"]),
        peak_price=float(ed.get("peak_price") or tc.get("close") or 0.0),
        vdiv=float(ed.get("velocity_divergence") or 0.0),
        ppte=float(ed.get("price_per_tick_efficiency") or 0.0),
        pips=float(c["pips"]),
    ))
print(f"joined entries: {len(entries)}")

# ---- 2. bars: exhaustion features (same as realized-pips probe) ----
bars = {}
ticks = {}
for sym in ("EURUSD", "USDJPY"):
    z = np.load(SCRATCH / f"realtick90_{sym}.npz")
    bars[sym] = (np.asarray(z["b_time"], np.int64), np.asarray(z["b_high"], float),
                 np.asarray(z["b_low"], float), np.asarray(z["b_close"], float))
    tmsc = np.asarray(z["tmsc"], np.int64)
    tsec = tmsc // 1000 if tmsc[0] > 1e11 else tmsc
    ticks[sym] = (tsec, (np.asarray(z["bid"], float) + np.asarray(z["ask"], float)) / 2.0)


def er(cl):
    net = abs(cl[-1] - cl[0]); path = np.sum(np.abs(np.diff(cl)))
    return float(net / path) if path > 0 else 0.0


# ---- 3. reconstruct exhaustion features + MFE ----
covered = mfe_ok = 0
for e in entries:
    bt, bh, bl, bc = bars[e["sym"]]
    pip = pipsz(e["sym"])
    j = int(np.searchsorted(bt, e["open_time"], side="right"))
    if j < 20:
        e["ok"] = False
        continue
    lo = max(0, j - 60)
    cl, hi, low = bc[lo:j], bh[lo:j], bl[lo:j]
    if len(cl) < 20:
        e["ok"] = False
        continue
    rb_hi = hi[-20:].max(); rb_lo = low[-20:].min(); span = (rb_hi - rb_lo) or 1e-9
    rng14 = (hi[-14:] - low[-14:]); atr = float(rng14.mean()) if len(rng14) else span
    price = e["peak_price"] or cl[-1]; rp = (price - rb_lo) / span
    e["er20"] = er(cl[-20:]); e["er60"] = er(cl)
    e["width_atr"] = float(span / atr) if atr > 0 else 0.0
    e["extreme"] = float(abs(rp - 0.5) * 2.0)
    e["ok"] = True
    covered += 1
    # --- MFE from ticks ---
    ts, mid = ticks[e["sym"]]
    a = int(np.searchsorted(ts, e["open_time"]))
    b = int(np.searchsorted(ts, e["open_time"] + 1800))     # search fill within 2 bars
    e["mfe"] = None
    if b > a:
        seg = mid[a:b]
        fi = int(np.argmin(np.abs(seg - e["fill"])))         # fill tick (closest mid to fill)
        if abs(seg[fi] - e["fill"]) / pip <= FILL_TOL_PIPS:
            ft = ts[a + fi]                                   # fill time
            f = a + fi + 1                                    # STRICTLY after fill
            g = int(np.searchsorted(ts, ft + H_MIN * 60))
            if g > f:
                fwd = mid[f:g]
                fav = (fwd - e["fill"]) if e["buy"] else (e["fill"] - fwd)
                e["mfe"] = float(max(0.0, fav.max()) / pip)
                mfe_ok += 1
print(f"reconstructed features: {covered}/{len(entries)}  |  MFE anchored: {mfe_ok}\n")

E = [e for e in entries if e.get("ok") and e.get("mfe") is not None]

# ---- stats (same as realized-pips probe) ----
def rankdata(a):
    a = np.asarray(a, float); order = a.argsort(); ranks = np.empty(len(a), float)
    ranks[order] = np.arange(1, len(a) + 1)
    _, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt)); np.add.at(sums, inv, ranks)
    return (sums / cnt)[inv]


def pearson(a, b):
    a = np.asarray(a, float) - np.mean(a); b = np.asarray(b, float) - np.mean(b)
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def spearman(a, b):
    return pearson(rankdata(a), rankdata(b))


def zc(x):
    x = np.asarray(x, float); s = x.std()
    return (x - x.mean()) / s if s > 0 else x * 0.0


FEATURES = [
    ("er20 (low=exhausted)", "er20", False),
    ("er60 (low=exhausted)", "er60", False),
    ("width_atr (wide=exhausted)", "width_atr", True),
    ("extreme (edge=exhausted)", "extreme", True),
    ("velocity_divergence (hi)", "vdiv", True),
    ("ppte (low=exhausted)", "ppte", False),
]


def run(label, rows):
    if len(rows) < 24:
        print(f"### {label}: n={len(rows)} too small\n")
        return
    mfe = np.array([r["mfe"] for r in rows])
    print(f"### {label}  (n={len(rows)}, MFE mean {mfe.mean():.2f}p, median {np.median(mfe):.2f}p)")
    print(f"{'feature':<32}{'spearman':>10}{'shuf-p':>9}   most-exh  least-exh   gap(p)   gap-p")
    comp = zc([-r["er20"] for r in rows]) + zc([r["width_atr"] for r in rows]) + zc([r["extreme"] for r in rows])
    rmfe = rankdata(mfe)
    for name, key, hi_exh in FEATURES + [("COMPOSITE (low-ER+wide+edge)", None, True)]:
        vals = comp if key is None else np.array([r[key] for r in rows])
        oriented = vals if hi_exh else -np.asarray(vals, float)
        rf = rankdata(oriented)
        sp = pearson(rf, rmfe)
        cnt = sum(1 for _ in range(NSHUF) if abs(pearson(rf, RNG.permutation(rmfe))) >= abs(sp) - 1e-12)
        p_sp = (cnt + 1) / (NSHUF + 1)
        q1, q2 = np.quantile(oriented, [1 / 3, 2 / 3])
        hi_g = mfe[oriented >= q2]; lo_g = mfe[oriented <= q1]
        gap = float(hi_g.mean() - lo_g.mean())
        mh = oriented >= q2; ml = oriented <= q1; cnt2 = 0
        for _ in range(NSHUF):
            s = RNG.permutation(mfe)
            if abs(s[mh].mean() - s[ml].mean()) >= abs(gap) - 1e-12:
                cnt2 += 1
        p_gap = (cnt2 + 1) / (NSHUF + 1)
        flag = "  <== " if (p_sp < 0.05 or p_gap < 0.05) else ""
        print(f"{name:<32}{sp:>+10.3f}{p_sp:>9.3f}   {hi_g.mean():>+7.2f}  {lo_g.mean():>+8.2f}  {gap:>+7.2f}  {p_gap:>6.3f}{flag}")
    print()


print(f"=== MFE TIEBREAKER (forward horizon {H_MIN}min, favorable excursion vs fill, strictly post-fill) ===\n")
run("COMBINED", E)
run("EURUSD", [e for e in E if e["sym"] == "EURUSD"])
run("USDJPY", [e for e in E if e["sym"] == "USDJPY"])
# sanity: does MFE even correlate with realized pips? (it should, loosely)
if E:
    print("sanity: spearman(MFE, realized pips) = %.3f  (expect positive — better entries run further AND book more)"
          % spearman([e["mfe"] for e in E], [e["pips"] for e in E]))
print("VERDICT RULE: a real entry-quality grade = a feature splitting MFE with p<0.05, surviving per-symbol, fade-thesis direction.")
print("[DONE-MARKER]")
