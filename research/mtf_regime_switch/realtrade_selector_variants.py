"""Selector VARIANTS scorer (READ-ONLY. Never trades). Extends realtrade_selector_score with:
  (A) skip_up_buy OFF   — the live scorer showed it skips uptrend WINNERS; test taking them.
  (B) A + RANGE location sub-rule — in a RANGE regime, don't 'take everything': fade only the
      EDGES (SELL upper part / BUY lower part), skip the middle. This targets the RANGE bucket,
      which is the whole bleed. Range position from the 20 M15 candles before entry.

Scores each policy by the net P&L of the trades it KEEPS (want high) vs SKIPS (want negative).
Run: .venv/Scripts/python.exe research/mtf_regime_switch/realtrade_selector_variants.py [days] [htf] [range_edge]
"""
import sys, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
try:
    from research.mtf_regime_switch.good_spot import good_spot_decision, htf_trend
except Exception:
    from good_spot import good_spot_decision, htf_trend

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
HTF = sys.argv[2] if len(sys.argv) > 2 else "2D"
REDGE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.50   # RANGE sub-rule: SELL needs pos>=1-edge... see below
SERVER_OFFSET_H = 3
MATCH_TOL_S = 900


def load_positions(days):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5-INIT-FAIL", mt5.last_error()); return [], {}
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=days), now + timedelta(hours=4)) or []
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
        d = abs(ep - tutc)
        if bd is None or d < bd:
            bd = d; best = mtf
    return best, bd


def range_pos(p, bars):
    b = [x for x in bars.get(p["sym"], []) if x["time"] < p["entry_t"]][-20:]
    if len(b) < 8:
        return None
    hi = max(x["high"] for x in b); lo = min(x["low"] for x in b); span = (hi - lo) or 1e-9
    return (p["entry_px"] - lo) / span     # 0 bottom .. 1 top


def range_subrule(direction, pos, edge):
    """In RANGE: keep only edge fades. SELL wants pos high (>=1-edge=top); BUY wants pos low (<=edge)."""
    if pos is None:
        return "take"                     # no range info -> don't override
    if direction == "SELL":
        return "take" if pos >= (1.0 - edge) else "skip"
    else:
        return "take" if pos <= edge else "skip"


def policy_base(p):    # current live-candidate selector
    return good_spot_decision(p["dir"], p["mtf"], htf_key=HTF, skip_up_buy=True)["action"]

def policy_A(p):       # skip_up_buy OFF
    return good_spot_decision(p["dir"], p["mtf"], htf_key=HTF, skip_up_buy=False)["action"]

def policy_B(p):       # A + RANGE location sub-rule
    a = good_spot_decision(p["dir"], p["mtf"], htf_key=HTF, skip_up_buy=False)["action"]
    if a == "take" and p["regime"] == "RANGE":
        return range_subrule(p["dir"], p.get("pos"), REDGE)
    return a


def score(rows, policy):
    keep = [r for r in rows if policy(r) == "take"]
    skip = [r for r in rows if policy(r) != "take"]
    kn = sum(r["net"] for r in keep); sn = sum(r["net"] for r in skip)
    kw = sum(1 for r in keep if r["net"] > 0)
    return keep, skip, kn, sn, (100*kw/len(keep) if keep else 0)


def main():
    positions, bars = load_positions(DAYS)
    stamps = load_stamps()
    scored = []
    for p in positions:
        mtf, d = nearest(stamps, p["sym"], p["entry_t"] - SERVER_OFFSET_H*3600)
        if mtf is None or d is None or d > MATCH_TOL_S:
            continue
        p["mtf"] = mtf; p["regime"] = htf_trend(mtf, HTF) or "NO-READ"; p["pos"] = range_pos(p, bars)
        scored.append(p)

    print("="*86)
    print(f"SELECTOR VARIANTS  (HTF={HTF}, last {DAYS}d, RANGE edge={REDGE}, n={len(scored)} matched)")
    print("="*86)
    base_total = sum(r["net"] for r in scored)
    print(f"\nTAKE-ALL baseline (no selector): net={base_total:+.2f} over {len(scored)} trades")

    for name, pol in (("BASE (skip_up_buy ON)", policy_base),
                      ("(A) skip_up_buy OFF", policy_A),
                      ("(B) A + RANGE edge-only", policy_B)):
        keep, skip, kn, sn, kw = score(scored, pol)
        print(f"\n{name}")
        print(f"   KEEP n={len(keep):3} net={kn:+9.2f} win={kw:3.0f}%   |   SKIP n={len(skip):3} net={sn:+9.2f}")
        print(f"   => policy net (what you'd actually book) = {kn:+.2f}   (vs take-all {base_total:+.2f}; "
              f"delta {kn-base_total:+.2f})")

    # within-RANGE location separation (does pos sort winners from losers in a RANGE?)
    rng = [r for r in scored if r["regime"] == "RANGE" and r["pos"] is not None]
    print("\n" + "-"*86)
    print(f"WITHIN-RANGE location separation (n={len(rng)} RANGE trades):")
    def cell(rows, lbl):
        if rows:
            print(f"   {lbl:20} n={len(rows):3} net={sum(x['net'] for x in rows):+9.2f} "
                  f"win={100*sum(1 for x in rows if x['net']>0)/len(rows):3.0f}%")
    for d in ("SELL", "BUY"):
        dd = [r for r in rng if r["dir"] == d]
        cell([r for r in dd if r["pos"] >= 0.6], f"{d} top (>=.60)")
        cell([r for r in dd if 0.4 <= r["pos"] < 0.6], f"{d} middle")
        cell([r for r in dd if r["pos"] < 0.4], f"{d} bottom (<.40)")
    print("-"*86)


if __name__ == "__main__":
    main()
