#!/usr/bin/env python3
"""EXHAUSTION MFE TIEBREAKER v2 (read-only; never trades).

Fixes v1's two flaws:
  * coverage — anchor the forward window at the entry bar CLOSE (open_time+900,
    guaranteed post-fill, no look-ahead, no fragile price-matching) -> all 301.
  * volatility artifact — measure BOTH MFE and MAE (max favorable / adverse
    excursion vs fill over the next H min) and grade the VOLATILITY-NEUTRAL ratio
    favret = MFE/(MFE+MAE). A wide-range setup that just swings big both ways has
    favret ~ 0.5 and scores no edge; only a DIRECTIONAL entry-quality edge moves it.
Grades exhaustion features against MFE (raw) and favret (neutral), 20k-null,
combined + per symbol. Reports sanity corr(MFE,pips) and corr(favret,pips)."""
import json
from pathlib import Path
import numpy as np

SCRATCH = Path(r"C:\Users\User\AppData\Local\Temp\claude\F--AxonAi-main\946c1cf8-7386-4149-986e-ed301bd7e0f9\scratchpad")
LOG = Path(r"F:\AxonAi_main\reports\signals.jsonl")
RNG = np.random.default_rng(20260817)
NSHUF = 20000
H_MIN = 60


def canon(s):
    s = (s or "").upper().replace(".I", "").replace(".", "").strip()
    return s[:6]


def pipsz(sym):
    return 0.01 if ("JPY" in sym or "XAU" in sym) else 0.0001


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
        sym=canon(d.get("mt5_symbol")), buy=str(d.get("decision", "")).lower().startswith("b"),
        open_time=int(tc["open_time"]), fill=float(tr["price"]),
        peak_price=float(ed.get("peak_price") or tc.get("close") or 0.0),
        vdiv=float(ed.get("velocity_divergence") or 0.0), ppte=float(ed.get("price_per_tick_efficiency") or 0.0),
        pips=float(c["pips"])))
print(f"joined entries: {len(entries)}")

bars, ticks = {}, {}
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
    ts, mid = ticks[e["sym"]]
    a0 = e["open_time"] + 900                       # anchor = bar close (post-fill)
    f = int(np.searchsorted(ts, a0)); g = int(np.searchsorted(ts, a0 + H_MIN * 60))
    e["mfe"] = e["mae"] = e["favret"] = None
    if g > f:
        fwd = mid[f:g]
        fav = (fwd - e["fill"]) if e["buy"] else (e["fill"] - fwd)
        mfe = max(0.0, float(fav.max())) / pip
        mae = max(0.0, float((-fav).max())) / pip
        e["mfe"] = mfe; e["mae"] = mae
        e["favret"] = mfe / (mfe + mae) if (mfe + mae) > 0 else 0.5
        mfe_ok += 1
print(f"features: {covered}/{len(entries)}  |  MFE/MAE anchored: {mfe_ok}\n")

E = [e for e in entries if e.get("ok") and e.get("mfe") is not None]


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


FEATURES = [("er20 (low=exh)", "er20", False), ("er60 (low=exh)", "er60", False),
            ("width_atr (wide=exh)", "width_atr", True), ("extreme (edge=exh)", "extreme", True),
            ("velocity_divergence", "vdiv", True), ("ppte (low=exh)", "ppte", False)]


def run(label, rows, target):
    if len(rows) < 24:
        print(f"### {label} [{target}]: n={len(rows)} too small\n")
        return
    y = np.array([r[target] for r in rows])
    ry = rankdata(y)
    print(f"### {label} — target={target}  (n={len(rows)}, mean {y.mean():.3f})")
    print(f"{'feature':<24}{'spearman':>10}{'shuf-p':>9}")
    comp = zc([-r["er20"] for r in rows]) + zc([r["width_atr"] for r in rows]) + zc([r["extreme"] for r in rows])
    for name, key, hi_exh in FEATURES + [("COMPOSITE", None, True)]:
        vals = comp if key is None else np.array([r[key] for r in rows])
        oriented = vals if hi_exh else -np.asarray(vals, float)
        rf = rankdata(oriented)
        sp = pearson(rf, ry)
        cnt = sum(1 for _ in range(NSHUF) if abs(pearson(rf, RNG.permutation(ry))) >= abs(sp) - 1e-12)
        p = (cnt + 1) / (NSHUF + 1)
        print(f"{name:<24}{sp:>+10.3f}{p:>9.3f}{'   <==' if p < 0.05 else ''}")
    print()


print(f"=== MFE TIEBREAKER v2 (bar-close anchor, {H_MIN}min, MFE + volatility-neutral favret) ===\n")
print("sanity: spearman(MFE, pips)=%.3f   spearman(favret, pips)=%.3f   (favret should track pips better)"
      % (spearman([e["mfe"] for e in E], [e["pips"] for e in E]),
         spearman([e["favret"] for e in E], [e["pips"] for e in E])))
print("(MFE mean %.2fp / MAE mean %.2fp / favret mean %.3f)\n"
      % (np.mean([e["mfe"] for e in E]), np.mean([e["mae"] for e in E]), np.mean([e["favret"] for e in E])))
for tgt in ("mfe", "favret"):
    run("COMBINED", E, tgt)
    run("EURUSD", [e for e in E if e["sym"] == "EURUSD"], tgt)
    run("USDJPY", [e for e in E if e["sym"] == "USDJPY"], tgt)
print("READ: raw MFE rewards volatility (width). favret is volatility-neutral — an entry-quality")
print("edge must move FAVRET, fade-thesis direction, surviving per-symbol. Else the 5th state grades nothing.")
print("[DONE-MARKER]")
