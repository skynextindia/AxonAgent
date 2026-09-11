"""FORWARD-ONLY (out-of-sample) scorer for the frozen selector candidate. READ-ONLY.

Candidate policy (frozen 2026-09-10, shaped on the last-7d in-sample look):
  * good_spot_decision with skip_up_buy=False
  * PLUS a RANGE sub-rule: in a 2D=RANGE regime, KEEP a fade only at the range EDGE
    (SELL pos >= 1-EDGE = top / BUY pos <= EDGE = bottom), SKIP the middle & wrong end.

On the FIRST run it stamps reports/selector_forward_cutoff.txt with 'now' (true UTC). Every
later run scores ONLY positions opened at/after that cutoff — a clean out-of-sample test as
new trades close. No rule fitting happens here; the params below are fixed.

Run: .venv/Scripts/python.exe research/mtf_regime_switch/realtrade_selector_forward.py
"""
import os, json
from datetime import datetime, timezone, timedelta

import sys
sys.path.insert(0, ".")
try:
    from research.mtf_regime_switch.good_spot import good_spot_decision, htf_trend
except Exception:
    from good_spot import good_spot_decision, htf_trend

# ── FROZEN candidate params (do not tune here) ──
HTF = "2D"
SKIP_UP_BUY = False
RANGE_EDGE = 0.50
SERVER_OFFSET_H = 3
MATCH_TOL_S = 900
CUTOFF_FILE = "reports/selector_forward_cutoff.txt"


def get_cutoff():
    if os.path.exists(CUTOFF_FILE):
        with open(CUTOFF_FILE) as f:
            return float(f.read().strip())
    now = datetime.now(timezone.utc).timestamp()
    os.makedirs("reports", exist_ok=True)
    with open(CUTOFF_FILE, "w") as f:
        f.write(str(now))
    return now


def load_positions():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5-INIT-FAIL", mt5.last_error()); return [], {}
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=45), now + timedelta(hours=4)) or []
    pos = {}
    for d in deals:
        if not d.symbol:
            continue
        p = pos.setdefault(d.position_id, {"sym": d.symbol, "dir": None, "entry_t": None,
            "entry_px": None, "net": 0.0})
        p["net"] += d.profit + d.commission + d.swap
        if d.entry == 0:
            p["dir"] = "SELL" if d.type == 1 else "BUY"; p["entry_t"] = d.time; p["entry_px"] = d.price
    positions = [p for p in pos.values() if p["entry_t"] and p["dir"]]
    bars = {}
    for s in set(p["sym"] for p in positions):
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M15, 0, 2600)
        bars[s] = list(r) if r is not None else []
    mt5.shutdown()
    return positions, bars


def load_stamps():
    rows = []
    with open("reports/signals.jsonl", encoding="utf-8") as f:
        for l in f:
            if "mtf_position" not in l:
                continue
            try:
                r = json.loads(l)
            except Exception:
                continue
            det = r.get("event_details") or {}
            mtf = det.get("mtf_position"); tu = r.get("timestamp_utc"); sym = r.get("mt5_symbol")
            if not (mtf and tu and sym):
                continue
            try:
                ep = datetime.strptime(tu, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                continue
            rows.append((sym, ep, mtf))
    return rows


def nearest(stamps, sym, tutc):
    best = None; bd = None
    for s, ep, mtf in stamps:
        if not s.startswith(sym[:6]):
            continue
        dd = abs(ep - tutc)
        if bd is None or dd < bd:
            bd = dd; best = mtf
    return best, bd


def range_pos(p, bars):
    b = [x for x in bars.get(p["sym"], []) if x["time"] < p["entry_t"]][-20:]
    if len(b) < 8:
        return None
    hi = max(x["high"] for x in b); lo = min(x["low"] for x in b); span = (hi - lo) or 1e-9
    return (p["entry_px"] - lo) / span


def decide(p):
    a = good_spot_decision(p["dir"], p["mtf"], htf_key=HTF, skip_up_buy=SKIP_UP_BUY)["action"]
    if a == "take" and p["regime"] == "RANGE" and p.get("pos") is not None:
        if p["dir"] == "SELL":
            return "take" if p["pos"] >= (1.0 - RANGE_EDGE) else "skip"
        return "take" if p["pos"] <= RANGE_EDGE else "skip"
    return a


def main():
    cutoff = get_cutoff()          # server-frame trades: keep if (entry_t - 3h) >= cutoff(true utc)
    cutoff_str = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    positions, bars = load_positions()
    stamps = load_stamps()
    fwd = []
    for p in positions:
        if (p["entry_t"] - SERVER_OFFSET_H * 3600) < cutoff:
            continue               # opened before the freeze -> not out-of-sample
        mtf, d = nearest(stamps, p["sym"], p["entry_t"] - SERVER_OFFSET_H * 3600)
        if mtf is None or d is None or d > MATCH_TOL_S:
            continue
        p["mtf"] = mtf; p["regime"] = htf_trend(mtf, HTF) or "NO-READ"; p["pos"] = range_pos(p, bars)
        p["verdict"] = decide(p)
        fwd.append(p)

    print("=" * 84)
    print(f"FORWARD-ONLY selector score   (frozen candidate; OOS since {cutoff_str})")
    print(f"params: HTF={HTF}  skip_up_buy={SKIP_UP_BUY}  RANGE_edge={RANGE_EDGE}")
    print("=" * 84)
    if not fwd:
        print("\nNo out-of-sample trades yet (nothing opened since the freeze). Re-run as trades close.")
        return
    keep = [p for p in fwd if p["verdict"] == "take"]
    skip = [p for p in fwd if p["verdict"] != "take"]
    kn = sum(p["net"] for p in keep); sn = sum(p["net"] for p in skip); tot = sum(p["net"] for p in fwd)
    kw = 100 * sum(1 for p in keep if p["net"] > 0) / len(keep) if keep else 0
    print(f"\nOOS trades so far: {len(fwd)}")
    print(f"  KEEP n={len(keep):3} net={kn:+9.2f} win={kw:3.0f}%")
    print(f"  SKIP n={len(skip):3} net={sn:+9.2f}   (want NEGATIVE)")
    print(f"  policy net booked = {kn:+.2f}   vs take-all {tot:+.2f}   (delta {kn-tot:+.2f})")
    print("\n  by regime:")
    for reg in ("UP", "DOWN", "RANGE", "NO-READ"):
        rr = [p for p in fwd if p["regime"] == reg]
        if rr:
            k = [p for p in rr if p["verdict"] == "take"]
            print(f"    {reg:8} n={len(rr):3} | keep {len(k)} net={sum(p['net'] for p in k):+8.2f} "
                  f"| skip {len(rr)-len(k)} net={sum(p['net'] for p in rr if p['verdict']!='take'):+8.2f}")
    print("\n  ARM only after enough OOS trades across UP/DOWN/RANGE with KEEP net > take-all and SKIP net < 0.")


if __name__ == "__main__":
    main()
