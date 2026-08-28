"""Does an ASYMMETRIC exit make the fade profitable ACROSS REGIMES?

The trade journal's MFE/MAE is single-regime (all August), so we can't answer this
from trades. Instead replay a fade-with-bracket strategy on 2 years of H1 bars
(multiple regimes), simulate each (SL, TP) bracket bar-by-bar with the REAL forward
path, net of cost, and bucket by quarter. Conservative by default: if one H1 bar's
range touches BOTH the stop and the target, assume the STOP hit first (pessimistic).
An `optimistic` pass (target-first) gives the upper bound.

Entry proxy = the fade: SHORT at a local swing high, LONG at a local swing low
(exhaustion of a move). Direction is ~a random walk, so the exact entry matters far
less than the exit structure — which is the whole point of the test.

    python -m research.exit_cut_forensics.exit_asymmetry_regime_test
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict

PIP = 0.0001
COST_PIPS = 2.0          # ~commission + spread per round trip (EURUSD, ~0.5 lot)
SWING_K = 4              # local-extreme window (bars each side, causal: uses [i-K, i])
MAX_HOLD = 120           # H1 bars a trade can stay open (~5 trading days; matches overnight holds)
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")


def load_h1():
    bars = []
    with open(os.path.join(_ROOT, "eurusd_h1_2y.csv")) as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat((r.get("Datetime") or r["Date"]).replace(" ", "T"))
                dt = dt.astimezone(timezone.utc)
                bars.append((dt, float(r["High"]), float(r["Low"]), float(r["Close"])))
            except Exception:
                continue
    bars.sort(key=lambda x: x[0])
    return bars


def quarter(dt):
    return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def simulate(bars, sl_p, tp_p, optimistic, momentum=False):
    """Return list of (quarter, net_pips) for every entry under this bracket.
    fade      = enter AGAINST the move (short a local high, long a local low).
    momentum  = enter WITH the move   (long a local high, short a local low)."""
    out = []
    n = len(bars)
    for i in range(SWING_K, n - 1):
        _, hi, lo, c = bars[i]
        win = bars[i - SWING_K:i + 1]
        is_high = hi >= max(b[1] for b in win)
        is_low = lo <= min(b[2] for b in win)
        if is_high == is_low:                # skip if both/neither
            continue
        # fade shorts a high / longs a low; momentum does the opposite.
        short = is_low if momentum else is_high
        entry = c
        sl = sl_p * PIP; tp = tp_p * PIP
        outcome = None
        for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
            _, bh, bl, bc = bars[j]
            if short:
                hit_sl = bh >= entry + sl       # adverse = up
                hit_tp = bl <= entry - tp       # favorable = down
            else:
                hit_sl = bl <= entry - sl       # adverse = down
                hit_tp = bh >= entry + tp       # favorable = up
            if hit_sl and hit_tp:
                outcome = tp_p if optimistic else -sl_p
                break
            if hit_tp:
                outcome = tp_p; break
            if hit_sl:
                outcome = -sl_p; break
        if outcome is None:                     # never resolved -> mark-to-close at last bar
            lastc = bars[min(i + MAX_HOLD, n - 1)][3]
            outcome = ((entry - lastc) if short else (lastc - entry)) / PIP
        out.append((quarter(bars[i][0]), outcome - COST_PIPS))
    return out


def agg(vals):
    """vals = list of net-pip floats -> (n, avg, total)."""
    n = len(vals)
    if n == 0:
        return (0, 0.0, 0.0)
    net = sum(vals)
    return (n, net / n, net)


ER_WIN = 24


def trailing_er(bars, i, win=ER_WIN):
    """Kaufman efficiency ratio over the last `win` closes ending at i (causal)."""
    if i < win:
        return 0.0
    seg = [b[3] for b in bars[i - win:i + 1]]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[k] - seg[k - 1]) for k in range(1, len(seg)))
    return net / path if path > 0 else 0.0


def simulate_switch(bars, sl_p, tp_p, er_thr, flat_range):
    """REACTIVE regime switch (no prediction): at each entry measure trailing ER.
    ER >= thr (trending) -> MOMENTUM; else -> FADE (or skip if flat_range). Conservative."""
    out = []
    n = len(bars)
    sl = sl_p * PIP; tp = tp_p * PIP
    for i in range(max(SWING_K, ER_WIN), n - 1):
        _, hi, lo, c = bars[i]
        win = bars[i - SWING_K:i + 1]
        is_high = hi >= max(b[1] for b in win)
        is_low = lo <= min(b[2] for b in win)
        if is_high == is_low:
            continue
        er = trailing_er(bars, i)
        if er >= er_thr:
            short = is_low               # trending -> momentum
        elif flat_range:
            continue                     # ranging -> stand aside
        else:
            short = is_high              # ranging -> fade
        entry = c; outcome = None
        for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
            _, bh, bl, bc = bars[j]
            if short:
                hit_sl = bh >= entry + sl; hit_tp = bl <= entry - tp
            else:
                hit_sl = bl <= entry - sl; hit_tp = bh >= entry + tp
            if hit_sl and hit_tp:
                outcome = -sl_p; break   # conservative
            if hit_tp:
                outcome = tp_p; break
            if hit_sl:
                outcome = -sl_p; break
        if outcome is None:
            lastc = bars[min(i + MAX_HOLD, n - 1)][3]
            outcome = ((entry - lastc) if short else (lastc - entry)) / PIP
        out.append((quarter(bars[i][0]), outcome - COST_PIPS))
    return out


GRID_SL = [8, 10, 12, 15, 20]
GRID_TP = [20, 40, 60, 80, 100, 120]   # widened to test "let winners run" further


def run_grid(bars, qs, momentum):
    label = "MOMENTUM — enter WITH the move (long highs / short lows)" if momentum \
        else "FADE — enter AGAINST the move (short highs / long lows)"
    print(f"\n========== {label} ==========")
    print("CONSERVATIVE (stop-first on ambiguous bars) — avg net pips/trade")
    hdr = "SL|TP"
    print(f"{hdr:>6}" + "".join(f"{tp:>8}" for tp in GRID_TP))
    robust = []; best = None
    for sl in GRID_SL:
        line = f"{sl:>6}"
        for tp in GRID_TP:
            rows = simulate(bars, sl, tp, optimistic=False, momentum=momentum)
            byq = defaultdict(list)
            for q, p in rows:
                byq[q].append(p)
            per_q = {q: agg(v)[1] for q, v in byq.items()}
            overall = agg([p for _, p in rows])[1]
            line += f"{overall:>8.2f}"
            if best is None or overall > best[2]:
                best = (sl, tp, overall, per_q)
            if all(x > 0 for x in per_q.values()) and len(per_q) >= 4:
                robust.append((sl, tp, overall, per_q))
        print(line)
    print("ROBUST (net > 0 EVERY quarter): " +
          ("NONE" if not robust else ", ".join(f"SL{s}/TP{t}({o:+.2f})" for s, t, o, _ in robust)))
    if best:
        s, t, o, pq = best
        print(f"BEST overall cell: SL={s} TP={t} (1:{t/s:.1f}) = {o:+.2f}p/trade  "
              f"worst-quarter {min(pq.values()):+.2f}p  ({sum(1 for x in pq.values() if x>0)}/{len(pq)} quarters positive)")
    return best


def main() -> int:
    bars = load_h1()
    qs = sorted({quarter(b[0]) for b in bars})
    print(f"H1 bars {len(bars)}  ({bars[0][0].date()}..{bars[-1][0].date()})  {len(qs)} quarters")
    print(f"cost={COST_PIPS}p/trade  swing_k={SWING_K}  max_hold={MAX_HOLD}h")

    best_f = run_grid(bars, qs, momentum=False)
    best_m = run_grid(bars, qs, momentum=True)

    # ---- COST SENSITIVITY: gross edge of the best cells + breakeven cost ----
    print("\n========== COST SENSITIVITY (best wide-TP cells) ==========")
    for name, best in (("FADE", best_f), ("MOMENTUM", best_m)):
        sl, tp = best[0], best[1]
        rows = simulate(bars, sl, tp, optimistic=False, momentum=(name == "MOMENTUM"))
        gross = agg([p for _, p in rows])[1] + COST_PIPS      # add cost back
        print(f"  {name} SL{sl}/TP{tp}: gross {gross:+.2f}p/trade -> net at cost "
              f"[0.5={gross-0.5:+.2f}  1.0={gross-1.0:+.2f}  1.5={gross-1.5:+.2f}  2.0={gross-2.0:+.2f}]"
              f"  breakeven cost ~{gross:.2f}p")

    # ---- REACTIVE REGIME SWITCH: momentum in trend / fade|flat in range ----
    print("\n========== REACTIVE REGIME SWITCH (trailing ER at entry, no prediction) ==========")
    for er_thr in (0.30, 0.40, 0.50):
        for flat in (False, True):
            rows = simulate_switch(bars, 15, 60, er_thr, flat)
            byq = defaultdict(list)
            for q, p in rows:
                byq[q].append(p)
            per_q = {q: agg(v)[1] for q, v in byq.items()}
            overall = agg([p for _, p in rows])[1]
            gross = overall + COST_PIPS
            pos = sum(1 for x in per_q.values() if x > 0)
            mode = "mom-in-trend / FLAT-in-range" if flat else "mom-in-trend / fade-in-range"
            print(f"  ER>={er_thr:.2f} SL15/TP60 [{mode}]: n={len(rows):5d} "
                  f"net {overall:+.2f}p (gross {gross:+.2f}) {pos}/{len(per_q)} qtrs+ breakeven~{gross:.2f}p")

    # ---- WIDER-TP + LIMIT-ENTRY: best fade bracket, per-quarter, by execution cost ----
    # A fade is naturally a LIMIT order (rest at the level), which saves the entry spread.
    # Modelled as lower per-trade cost: market 2.0p / limit-entry 1.2p / raw+limit 0.7p.
    # (Entry stays at close = conservative; a real limit fill would be at least as good.)
    print("\n========== WIDER-TP FADE @ LIMIT EXECUTION ==========")
    sl, tp = best_f[0], best_f[1]
    rows = simulate(bars, sl, tp, optimistic=False, momentum=False)
    byq = defaultdict(list)
    for q, p in rows:
        byq[q].append(p + COST_PIPS)             # strip the 2p back out -> GROSS pips
    print(f"  best fade bracket SL={sl}/TP={tp} (1:{tp/sl:.1f})   per-quarter gross then net by cost")
    print(f"    {'quarter':9}{'gross':>8}{'mkt2.0':>8}{'lim1.2':>8}{'raw0.7':>8}")
    for q in qs:
        if q in byq:
            g = agg(byq[q])[1]
            print(f"    {q:9}{g:+8.2f}{g-2.0:+8.2f}{g-1.2:+8.2f}{g-0.7:+8.2f}")
    allg = agg([p + COST_PIPS for _, p in rows])[1]
    print(f"    {'OVERALL':9}{allg:+8.2f}{allg-2.0:+8.2f}{allg-1.2:+8.2f}{allg-0.7:+8.2f}")

    def posq(c):
        return sum(1 for q in byq if agg(byq[q])[1] - c > 0)
    nq = len(byq)
    print(f"    quarters positive:   market(2.0) {posq(2.0)}/{nq}   "
          f"limit(1.2) {posq(1.2)}/{nq}   raw(0.7) {posq(0.7)}/{nq}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
