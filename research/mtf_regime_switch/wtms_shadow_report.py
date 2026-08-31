"""Reader for the live wide-TP MTF-regime shadow (reports/wide_tp_mtf_shadow.jsonl).

Rebuilds the forward good-spot map from REAL live setups: for each gated fade signal
the daemon logged the fade outcome, the with-trend (opposite) outcome, and the live
MTF cross-regime. This scores the wide-TP + MTF-adaptive idea out-of-sample, net of
cost, so we can decide at the Sept-21 checkpoint WITHOUT having traded it.

    python -m research.mtf_regime_switch.wtms_shadow_report [reports/wide_tp_mtf_shadow.jsonl]
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from statistics import mean

COSTS = [0.7, 1.2, 2.0]
DEFAULT = os.path.join("reports", "wide_tp_mtf_shadow.jsonl")


def htf_state(row, key="1D"):
    """Higher-TF trend from the live MTF stamp: '1D' (or '1W'/'1H') -> UP/DOWN/RANGE."""
    tfs = row.get("mtf_tfs") or {}
    v = tfs.get(key)
    if isinstance(v, (list, tuple)) and v:
        return str(v[0]).upper()
    return "?"


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("type") == "wtms":
                    rows.append(r)
            except Exception:
                continue
    return rows


def summ(label, vals):
    if not vals:
        print(f"  {label:44} n=0"); return
    n = len(vals)
    at = {c: mean([v - c for v in vals]) for c in COSTS}
    win = 100.0 * sum(1 for v in vals if v > 50) / n
    print(f"  {label:44} n={n:4d}  win{win:3.0f}%  net@[0.7 {at[0.7]:+5.2f} | 1.2 {at[1.2]:+5.2f} | 2.0 {at[2.0]:+5.2f}]")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    rows = [r for r in load(path) if str(r.get("mt5_symbol", "")).upper().startswith("EURUSD")]
    print(f"wide-TP MTF shadow: {len(rows)} resolved setups from {path}")
    if not rows:
        print("  (no rows yet: the shadow logs a row only after BOTH legs resolve, ~up to 5 days")
        print("   per setup. Let it run; re-check at the checkpoint.)")
        return 0
    fade = [r["fade_pips"] for r in rows]
    wt = [r["withtrend_pips"] for r in rows]
    print("\n== overall (gross pips; net at 3 cost levels) ==")
    summ("ALWAYS FADE", fade)
    summ("ALWAYS WITH-TREND", wt)

    print("\n== fade outcome by HTF (1D) regime ==")
    byreg = defaultdict(list)
    for r in rows:
        byreg[htf_state(r)].append(r["fade_pips"])
    for reg in ("UP", "DOWN", "RANGE", "?"):
        if byreg.get(reg):
            summ(f"HTF {reg}: fade", byreg[reg])

    print("\n== the good-spot rules (rebuilt on live data) ==")
    def pick(r):
        h = htf_state(r); fd = r["fade_dir"]
        # sell rallies in a downtrend (fade a high = Sell) / buy dips in an uptrend
        # (fade a low = Buy) / fade in range; skip the rest.
        if h == "RANGE":
            return r["fade_pips"]
        if h == "DOWN" and fd == "Sell":
            return r["fade_pips"]
        if h == "UP" and fd == "Buy":
            return r["fade_pips"]
        return None
    gs = [v for v in (pick(r) for r in rows) if v is not None]
    summ("GOOD-SPOTS (align fade to HTF / fade range)", gs)
    # downtrend rally-sell alone (the single best backtest cell)
    rally = [r["fade_pips"] for r in rows if htf_state(r) == "DOWN" and r["fade_dir"] == "Sell"]
    summ("  of which: SELL rallies in downtrend", rally)

    print("\nNOTE: compare to the backtest (research/mtf_regime_switch/mtf_cross_regime_spots.py):")
    print("GOOD-SPOTS was +1.11p @limit(1.2). Decide at the checkpoint on n>=60 across >=2 regimes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
