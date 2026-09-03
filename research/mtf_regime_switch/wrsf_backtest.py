"""WRSF backtest — validate the Step-4 engine's DECISION code on real history.

Unlike mtf_cross_regime_spots.py (inline good-spot logic), this drives the CANONICAL
live functions: classify_tf (step-1 net/range trend) builds a per-entry mtf_stamp, and
good_spot_decision (step-2 selector) returns take/skip/flip. So the number here is what
the shipped selector would have done — including the buy-dip-in-uptrend bucket the old
backtest EXCLUDED (the memory flags that asymmetry as maybe non-generalizing; this tests it).

Entry proxy = a local swing high (Sell fade) / low (Buy fade), SWING_K bars each side.
Bracket = SL20/TP100, 5-day max hold, adverse-first. Reports SELECTOR net (take=fade,
flip=with-trend, skip=avoided) vs blind ALWAYS-FADE, by quarter, at 3 cost levels, for a
couple of HTF windows and flip on/off.

    python -m research.mtf_regime_switch.wrsf_backtest
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

from research.mtf_structure.structure import classify_tf
from research.mtf_regime_switch.good_spot import good_spot_decision

PIP = 0.0001; SWING_K = 6; MAXHOLD = 480; SL_P = 20; TP_P = 100
COSTS = [0.7, 1.2, 2.0]
HTF_WINDOWS = {"1D": 96, "2D": 192}         # M15 bars: 1 day = 96, 2 days = 192
_THIS = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m15_mt5.csv")


def load():
    b = []
    with open(M15) as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(r["Datetime"].replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            b.append((dt.astimezone(timezone.utc), float(r["High"]), float(r["Low"]), float(r["Close"])))
    b.sort(key=lambda x: x[0]); return b


def q(dt): return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def out(bars, i, entry, short, n):
    """Wide-bracket outcome in gross pips (adverse-first)."""
    sl = SL_P * PIP; tp = TP_P * PIP
    for j in range(i + 1, min(i + 1 + MAXHOLD, n)):
        _, bh, bl, bc = bars[j]
        if short:
            hs = bh >= entry + sl; ht = bl <= entry - tp
        else:
            hs = bl <= entry - sl; ht = bh >= entry + tp
        if hs: return -SL_P
        if ht: return TP_P
    last = bars[min(i + MAXHOLD, n - 1)][3]
    return (entry - last) / PIP if short else (last - entry) / PIP


def build_stamp(highs, lows, closes, i):
    """Minimal live-shaped stamp: tfs[key] = [trend, pos, net_range, er] via classify_tf.
    Passes ONLY the window slice (classify_tf reads the last `bars`), so each call is
    O(window), not O(i) — avoids an O(n^2) prefix copy."""
    tfs = {}
    for key, w in HTF_WINDOWS.items():
        s = i - w + 1
        if s < 0:
            continue
        t = classify_tf(key, highs[s:i + 1], lows[s:i + 1], closes[s:i + 1],
                        closes[i], PIP, bars=w, measure="net_range")
        if t is not None:
            tfs[key] = [t.trend, t.position_pct, t.net_range, t.efficiency_ratio]
    return {"tfs": tfs}


def main() -> int:
    bars = load(); n = len(bars)
    highs = [b[1] for b in bars]; lows = [b[2] for b in bars]; closes = [b[3] for b in bars]
    print(f"WRSF backtest: {n} M15 bars, SL{SL_P}/TP{TP_P} 5d hold, canonical selector\n")

    # entries: swing highs (Sell fade) / lows (Buy fade), with raw long/short outcomes + stamp
    E = []
    for i in range(max(SWING_K, max(HTF_WINDOWS.values())), n - 1):
        dt, hi, lo, c = bars[i]; win = bars[i - SWING_K:i + 1]
        ih = hi >= max(b[1] for b in win); il = lo <= min(b[2] for b in win)
        if ih == il:
            continue
        E.append({"i": i, "q": q(dt), "fade": "Sell" if ih else "Buy",
                  "short": out(bars, i, c, True, n), "long": out(bars, i, c, False, n),
                  "stamp": build_stamp(highs, lows, closes, i)})
    print(f"entries (swing highs/lows): {len(E)}\n")

    def pnl(e, direction):
        return e["short"] if direction == "Sell" else e["long"]

    def score(pick, cost):
        """pick(e) -> ('take'|'flip'|'skip', direction). Returns (n, avg, q+, nq)."""
        byq = defaultdict(list)
        for e in E:
            act, d = pick(e)
            if act == "skip":
                continue
            byq[e["q"]].append(pnl(e, d) - cost)
        allv = [p for v in byq.values() for p in v]
        if not allv:
            return (0, 0.0, 0, 0)
        perq = {k: mean(v) for k, v in byq.items()}
        return (len(allv), mean(allv), sum(1 for x in perq.values() if x > 0), len(perq))

    def line(label, pick):
        cells = []
        for cst in COSTS:
            nn, a, pos, nq = score(pick, cst)
            cells.append(f"{a:+5.2f}p({pos}/{nq}q)")
        nn = score(pick, COSTS[0])[0]
        print(f"  {label:44} n={nn:5d}  " + " | ".join(f"@{c}:{x}" for c, x in zip(COSTS, cells)))

    # blind baseline
    blind = lambda e: ("take", e["fade"])
    print("== ALWAYS-FADE (blind baseline) ==")
    line("always fade", blind)

    for hk in HTF_WINDOWS:
        print(f"\n== SELECTOR via good_spot_decision, htf_key={hk} ==")
        def sel(e, hk=hk, flip=False):
            d = good_spot_decision(e["fade"], e["stamp"], htf_key=hk, flip_counter_trend=flip)
            act = d["action"]
            direction = d.get("flip_to", e["fade"]) if act == "flip" else e["fade"]
            return (act, direction)
        line(f"SELECTOR skip-counter (flip OFF)", lambda e, hk=hk: sel(e, hk, False))
        line(f"SELECTOR flip-counter (flip ON)", lambda e, hk=hk: sel(e, hk, True))
        # decompose the take buckets to expose the buy-up-dip asymmetry
        def bucket(name, cond):
            sub = [e for e in E if cond(e)]
            if not sub:
                print(f"    {name:42} n=0"); return
            vals = [pnl(e, e["fade"]) - 1.2 for e in sub]
            byq = defaultdict(list)
            for e in sub:
                byq[e["q"]].append(pnl(e, e["fade"]) - 1.2)
            pq = sum(1 for v in byq.values() if mean(v) > 0)
            print(f"    {name:42} n={len(sub):5d}  avg{mean(vals):+5.2f}p @1.2  {pq}/{len(byq)}q+")
        htf = lambda e, hk=hk: (e["stamp"]["tfs"].get(hk) or ["?"])[0]
        print("    -- take-bucket decomposition (fade dir, @1.2p) --")
        bucket("RANGE fade", lambda e, hk=hk: htf(e, hk) == "RANGE")
        bucket("DOWN + Sell (sell rally, aligned)", lambda e, hk=hk: htf(e, hk) == "DOWN" and e["fade"] == "Sell")
        bucket("UP + Buy (buy dip, aligned)", lambda e, hk=hk: htf(e, hk) == "UP" and e["fade"] == "Buy")
        bucket("counter-trend (skipped/flipped)", lambda e, hk=hk:
               (htf(e, hk) == "DOWN" and e["fade"] == "Buy") or (htf(e, hk) == "UP" and e["fade"] == "Sell"))

    print("\nREAD: SELECTOR should beat ALWAYS-FADE at limit cost (1.2p) across >=2 quarters.")
    print("Watch the UP+Buy bucket — if it's a drag, the selector needs the down/up asymmetry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
