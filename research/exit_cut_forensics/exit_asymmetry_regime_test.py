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


GRID_SL = [8, 10, 12, 15, 20]
GRID_TP = [8, 12, 20, 30, 40, 60]


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

    run_grid(bars, qs, momentum=False)
    best_m = run_grid(bars, qs, momentum=True)

    # per-quarter detail for the best momentum bracket (conservative / optimistic)
    if best_m:
        sl, tp = best_m[0], best_m[1]
        print(f"\nPER-QUARTER detail — best MOMENTUM bracket SL={sl} TP={tp} (1:{tp/sl:.1f})  cons / optimistic:")
        cons = simulate(bars, sl, tp, optimistic=False, momentum=True)
        opti = simulate(bars, sl, tp, optimistic=True, momentum=True)
        bqc = defaultdict(list); bqo = defaultdict(list)
        for q, p in cons: bqc[q].append(p)
        for q, p in opti: bqo[q].append(p)
        for q in qs:
            if q in bqc:
                print(f"      {q}: {agg(bqc[q])[1]:+6.2f} / {agg(bqo[q])[1]:+6.2f}   (n={len(bqc[q])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
