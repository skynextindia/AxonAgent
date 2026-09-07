"""Re-entry distance reader — how far, in pips and minutes, does the engine re-open a
trade from where it last CLOSED one of the SAME symbol + direction, and does re-entering
close-by cost money?

Pulls closed positions from MT5 history (last --days, default 30), reconstructs each
position's entry/exit, then (via reentry_core) stamps the distance/gap from the prior
same-(symbol,direction) exit and buckets net P&L by "near re-entry" vs "rest".

The live engine gates re-entry ONLY by a time cooldown — it never measures distance from
its own last exit. This quantifies what that missing check would be worth.

Read-only. Never trades. Run with the Eightcap terminal open:
    .venv/Scripts/python.exe research/reentry_distance/reentry_report.py --days 30 --pips 5 --min 60
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta

from reentry_core import annotate_reentries, summarize   # same-dir imports when run from folder
try:
    from research.reentry_distance.reentry_core import annotate_reentries, summarize  # noqa
except Exception:
    pass


def load_positions(days: int):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5-INIT-FAIL", mt5.last_error()); return []
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=days), now + timedelta(hours=2)) or []
    mt5.shutdown()
    pos: dict = {}
    for d in deals:
        if not d.symbol:
            continue
        p = pos.setdefault(d.position_id, {"sym": d.symbol, "dir": None, "entry_t": None,
            "entry_px": None, "exit_t": None, "exit_px": None, "net": 0.0})
        p["net"] += d.profit + d.commission + d.swap
        if d.entry == 0:      # DEAL_ENTRY_IN
            p["dir"] = "SELL" if d.type == 1 else "BUY"
            p["entry_t"] = d.time; p["entry_px"] = d.price
        elif d.entry == 1:    # DEAL_ENTRY_OUT
            p["exit_t"] = d.time; p["exit_px"] = d.price
    # keep only fully-formed, closed positions
    return [p for p in pos.values() if p["entry_t"] and p["exit_t"] and p["entry_px"]]


def hm(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%m-%d %H:%M") if epoch else "----"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--pips", type=float, default=5.0, help="near-reentry pip threshold")
    ap.add_argument("--min", type=float, default=60.0, help="near-reentry minute threshold")
    a = ap.parse_args()

    positions = load_positions(a.days)
    if not positions:
        print("no closed positions in the window."); return
    ann = annotate_reentries(positions)
    res = summarize(ann, a.pips, a.min)

    print("=" * 72)
    print(f"RE-ENTRY DISTANCE  ({len(positions)} closed positions, last {a.days}d)")
    print(f"near re-entry = re-opened within {a.pips:g} pips AND {a.min:g} min of a same-dir exit")
    print("=" * 72)
    for lbl, b in (("NEAR re-entries", res["near"]), ("all other entries", res["rest"])):
        print(f"  {lbl:20} n={b['n']:<4} net={b['net']:+9.2f}  avg={b['avg']:+7.2f}  win%={b['win_pct']}")
    d = res["near"]["net"] - 0  # cost of the near bucket, as booked
    print("-" * 72)
    print(f"  net booked on NEAR re-entries = {res['near']['net']:+.2f}"
          f"  ({'a drag -- a distance guard would have skipped these' if res['near']['net'] < 0 else 'positive -- a guard would have cost money'})")
    if res["near"]["n"] < 10:
        print("  (n<10 near re-entries — indicative, not decisive; keep collecting.)")
    print("-" * 72)

    print("\nNEAR re-entry detail (each vs the same-dir exit it followed):")
    if not res["near_rows"]:
        print("  (none in window)")
    for r in sorted(res["near_rows"], key=lambda x: x["entry_t"]):
        print(f"  {hm(r['entry_t'])} {r['sym']:9} {r['dir']:4} @ {r['entry_px']}"
              f"  dist={r['dist_pips']}p  gap={r['gap_min']}min  net={float(r['net']):+.2f}")

    # full distance histogram (all entries that had a prior same-dir exit)
    withprior = [r for r in ann if r.get("dist_pips") is not None]
    if withprior:
        print(f"\ndistance-from-last-exit histogram ({len(withprior)} entries w/ a prior same-dir trade):")
        edges = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 1e9)]
        for lo, hi in edges:
            grp = [r for r in withprior if lo <= r["dist_pips"] < hi]
            if grp:
                net = sum(float(r["net"]) for r in grp)
                lbl = f"{lo:g}-{hi:g}p" if hi < 1e9 else f"{lo:g}p+"
                print(f"  {lbl:9} n={len(grp):<4} net={net:+9.2f}  avg={net/len(grp):+7.2f}")


if __name__ == "__main__":
    sys.exit(main())
