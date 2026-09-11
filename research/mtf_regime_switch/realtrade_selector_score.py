"""Real-trade good-spot selector scorer (READ-ONLY. Never trades).

The wide-TP shadow resolves too slowly (120h legs) to validate the selector. But every REAL
trade already carries the MTF regime on its entry signal, and real trades resolve in hours. So:
recompute good_spot_decision() for each closed position from the regime stamped on its entry
signal, join to the trade's ACTUAL realized P&L, and bucket by verdict x HTF-regime.

ARM the selector as a live SKIP-gate only when, on post-build live rows:
  * the trades it would SKIP are clearly net-NEGATIVE (it removes losers), AND
  * the trades it would KEEP (take) stay net-positive-or-better, AND
  * there is coverage across UP / DOWN / RANGE (a single-regime week proves nothing).

Run (Eightcap terminal open):
  .venv/Scripts/python.exe research/mtf_regime_switch/realtrade_selector_score.py [days] [htf]
"""
import sys, json
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
try:
    from research.mtf_regime_switch.good_spot import good_spot_decision, htf_trend
except Exception:
    from good_spot import good_spot_decision, htf_trend  # when run from the folder

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
HTF = sys.argv[2] if len(sys.argv) > 2 else "2D"
SERVER_OFFSET_H = 3           # MT5 deal.time epoch reads as server clock (~UTC+3); true UTC = deal.time - 3h
MATCH_TOL_S = 900             # accept a signal within 15 min of the entry as that trade's stamp
SIGNALS = "reports/signals.jsonl"


def load_positions(days):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5-INIT-FAIL", mt5.last_error()); return []
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=days), now + timedelta(hours=4)) or []
    mt5.shutdown()
    pos = {}
    for d in deals:
        if not d.symbol:
            continue
        p = pos.setdefault(d.position_id, {"sym": d.symbol, "dir": None, "entry_t": None,
            "entry_px": None, "net": 0.0})
        p["net"] += d.profit + d.commission + d.swap
        if d.entry == 0:
            p["dir"] = "SELL" if d.type == 1 else "BUY"
            p["entry_t"] = d.time; p["entry_px"] = d.price
    return [p for p in pos.values() if p["entry_t"] and p["dir"]]


def load_stamps():
    rows = []
    with open(SIGNALS, encoding="utf-8") as f:
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


def nearest_stamp(stamps, sym, true_utc):
    best = None; bestd = None
    for s, ep, mtf in stamps:
        if not s.startswith(sym[:6]):
            continue
        d = abs(ep - true_utc)
        if bestd is None or d < bestd:
            bestd = d; best = mtf
    return (best, bestd) if best is not None else (None, None)


def main():
    positions = load_positions(DAYS)
    stamps = load_stamps()
    if not positions:
        print("no closed positions."); return
    if not stamps:
        print("no MTF-stamped signals found."); return

    scored = []
    unmatched = 0
    for p in positions:
        true_utc = p["entry_t"] - SERVER_OFFSET_H * 3600
        mtf, dt = nearest_stamp(stamps, p["sym"], true_utc)
        if mtf is None or dt is None or dt > MATCH_TOL_S:
            unmatched += 1; continue
        gs = good_spot_decision(p["dir"], mtf, htf_key=HTF)
        regime = htf_trend(mtf, HTF) or "NO-READ"
        scored.append({**p, "verdict": gs["action"], "regime": regime, "match_s": dt})

    print("=" * 84)
    print(f"REAL-TRADE GOOD-SPOT SELECTOR SCORE   (HTF={HTF}, last {DAYS}d)")
    print(f"matched {len(scored)}/{len(positions)} closed positions to an entry-signal regime "
          f"(±{MATCH_TOL_S//60}min); unmatched {unmatched}")
    if scored:
        med = sorted(s["match_s"] for s in scored)[len(scored)//2]
        print(f"median match delta = {med:.0f}s")
    print("=" * 84)

    def block(rows, title):
        n = len(rows); net = sum(r["net"] for r in rows); w = sum(1 for r in rows if r["net"] > 0)
        wr = (100 * w / n) if n else 0
        print(f"  {title:22} n={n:3}  net={net:+9.2f}  avg={ (net/n) if n else 0:+7.2f}  win={wr:3.0f}%")

    print("\nBY VERDICT (what the selector would do):")
    for v in ("take", "skip", "flip"):
        rows = [s for s in scored if s["verdict"] == v]
        if rows:
            block(rows, f"{v.upper()}")

    print("\nBY VERDICT x REGIME:")
    for reg in ("UP", "DOWN", "RANGE", "NO-READ"):
        rr = [s for s in scored if s["regime"] == reg]
        if not rr:
            continue
        print(f"  --- HTF {reg} (n={len(rr)}) ---")
        for v in ("take", "skip", "flip"):
            rows = [s for s in rr if s["verdict"] == v]
            if rows:
                block(rows, f"  {v}")

    skip = [s for s in scored if s["verdict"] in ("skip", "flip")]
    take = [s for s in scored if s["verdict"] == "take"]
    sn = sum(s["net"] for s in skip); tn = sum(s["net"] for s in take)
    print("\n" + "-" * 84)
    print("ARMING READ:")
    print(f"  would-SKIP: n={len(skip)} net={sn:+.2f}  (want NEGATIVE — removing losers)")
    print(f"  would-KEEP: n={len(take)} net={tn:+.2f}  (want >= its current contribution)")
    regs = set(s["regime"] for s in scored)
    print(f"  regime coverage: {sorted(regs)}  "
          f"{'OK (multi-regime)' if len({'UP','DOWN','RANGE'} & regs) >= 2 else 'THIN — single regime, inconclusive'}")
    print("-" * 84)


if __name__ == "__main__":
    main()
