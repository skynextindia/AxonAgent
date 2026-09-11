"""Phase A — WRSF shadow report (READ-ONLY). Never trades.

The WRSF engine = take the good-spot-SELECTED fade at a fixed SL20/TP100, market entry, held to
TP/SL/~120h, EURUSD-only. The live wtms shadow (reports/wide_tp_mtf_shadow.jsonl) already logs
exactly that: the good_spot verdict + the resolved fade leg outcome per gated signal. So this
reader reconstructs the WRSF engine's P&L from those rows — no daemon change, no live risk.

Policy (locked 2026-09-11): market entry, real cost 0.70p (EURUSD), selector skip-counter (flip
OFF), EURUSD-only.
  * goodspot.action == take  -> WRSF trades the FADE      (pnl = fade_pips - COST)
  * goodspot.action == flip  -> WRSF trades WITH-TREND    (pnl = withtrend_pips - COST)  [flip OFF => absent]
  * goodspot.action == skip  -> no trade

Compares to BLIND wide-TP (take every fade). Reports net/quarter, by 2D regime, TP-reacher count,
and the arming-gate readiness (n>=60 across >=2 regimes, net>0 at 0.70p, selector beats blind).

Run: .venv/Scripts/python.exe research/mtf_regime_switch/wrsf_shadow_report.py
"""
import json, os
from collections import defaultdict
from datetime import datetime, timezone

SHADOW = "reports/wide_tp_mtf_shadow.jsonl"
COST = 0.70            # real EURUSD round-trip commission (pips); market entry, no limit needed
MIN_N = 60             # arming gate: min trades
MIN_REGIMES = 2        # arming gate: min distinct 2D regimes with trades


def load():
    if not os.path.exists(SHADOW):
        return []
    rows = []
    with open(SHADOW, encoding="utf-8") as f:
        for l in f:
            l = l.strip()
            if not l:
                continue
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def q(epoch):
    d = datetime.fromtimestamp(epoch, timezone.utc)
    return f"{d.year}Q{(d.month-1)//3+1}"


def regime_of(r):
    gs = r.get("goodspot") or {}
    if gs.get("htf"):
        return gs["htf"]
    tfs = r.get("mtf_tfs") or {}
    v = tfs.get("2D")
    return (str(v[0]).upper() if isinstance(v, (list, tuple)) and v else "NO-READ")


def wrsf_pnl(r):
    """WRSF trade P&L for this row under the locked policy, or None if WRSF skips it."""
    gs = r.get("goodspot") or {}
    act = gs.get("action")
    if act == "take":
        fp = r.get("fade_pips")
        return (fp - COST) if fp is not None else None
    if act == "flip":                       # absent while flip is OFF; handled for completeness
        wp = r.get("withtrend_pips")
        return (wp - COST) if wp is not None else None
    return None                              # skip / missing verdict -> no trade


def main():
    rows = load()
    print("=" * 82)
    print(f"WRSF PHASE-A SHADOW  (reader over {SHADOW}, cost={COST}p, EURUSD-only)")
    print("=" * 82)
    if not rows:
        print("\nNo resolved wtms rows yet (SL20/TP100 legs take up to 120h; file empty/absent).")
        print("This reader is BUILT and ready — it fills as the live wtms shadow resolves rows.")
        return

    eur = [r for r in rows if str(r.get("mt5_symbol", "")).startswith("EURUSD")]
    print(f"\nresolved wtms rows: {len(rows)} total, {len(eur)} EURUSD")

    # WRSF selected engine
    wrsf = [(r, wrsf_pnl(r)) for r in eur]
    traded = [(r, p) for r, p in wrsf if p is not None]
    byq = defaultdict(list); byreg = defaultdict(list); tp_reach = 0
    for r, p in traded:
        byq[q(r.get("sig_epoch", 0))].append(p)
        byreg[regime_of(r)].append(p)
        if (r.get("fade_pips") or 0) >= r.get("tp_pips", 100):
            tp_reach += 1
    net = sum(p for _, p in traded)
    wins = sum(1 for _, p in traded if p > 0)

    # blind baseline: take every fade
    blind = [(r.get("fade_pips") - COST) for r in eur if r.get("fade_pips") is not None]

    print(f"\n-- WRSF SELECTED engine --")
    print(f"  trades={len(traded)}  net={net:+.1f}p  avg={ (net/len(traded)) if traded else 0:+.2f}p  "
          f"win={ (100*wins/len(traded)) if traded else 0:.0f}%  TP-reachers={tp_reach}")
    print(f"  by quarter:")
    for k in sorted(byq):
        v = byq[k]; print(f"    {k}: n={len(v):3} net={sum(v):+7.1f}p avg={sum(v)/len(v):+.2f}p")
    print(f"  by 2D regime:")
    for k in sorted(byreg):
        v = byreg[k]; print(f"    {k:8} n={len(v):3} net={sum(v):+7.1f}p avg={sum(v)/len(v):+.2f}p")

    print(f"\n-- BLIND wide-TP (take every fade) --")
    if blind:
        print(f"  trades={len(blind)}  net={sum(blind):+.1f}p  avg={sum(blind)/len(blind):+.2f}p")
    print(f"  => selector edge vs blind: {(net/len(traded) if traded else 0) - (sum(blind)/len(blind) if blind else 0):+.2f}p/trade")

    # arming gate
    qpos = sum(1 for k in byq if sum(byq[k]) > 0)
    print(f"\n-- ARMING GATE --")
    ok_n = len(traded) >= MIN_N
    ok_reg = len([k for k in byreg if k in ("UP", "DOWN", "RANGE")]) >= MIN_REGIMES
    ok_net = net > 0
    ok_beat = traded and blind and (net/len(traded)) > (sum(blind)/len(blind))
    for name, ok in (("n>=60", ok_n), (">=2 regimes", ok_reg), ("net>0 @0.70p", ok_net),
                     ("beats blind", ok_beat), ("TP-reachers present", tp_reach > 0)):
        print(f"    [{'PASS' if ok else 'wait'}] {name}")
    print(f"\n  {'READY to consider arming (Phase D)' if all((ok_n,ok_reg,ok_net,ok_beat,tp_reach>0)) else 'NOT yet — keep collecting forward'}")


if __name__ == "__main__":
    main()
