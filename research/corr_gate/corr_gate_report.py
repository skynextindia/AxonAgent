"""Corr-gate shadow reader — did skipping the follower leg in the LOCKSTEP regime
cut the double-losses?

Reads reports/corr_gate_shadow.jsonl (one row per inverse-mirror fire, stamped with the
live EURUSD<->USDJPY rolling correlation + a SKIP/FIRE verdict + both legs' tickets),
joins each leg's REALIZED P&L from MT5 history by position_id, and buckets combined P&L
by verdict.

The gate, if armed live, withholds ONLY the follower (USDJPY) leg on SKIP-verdict fires.
So the counterfactual saving of arming it = -sum(follower_pnl) over the SKIP rows:
  * follower lost on those rows  -> gate ADDS that back (good)
  * follower won on those rows   -> gate COSTS us (bad)

Read-only. Never trades. Run with the Eightcap terminal open:
    .venv/Scripts/python.exe research/corr_gate/corr_gate_report.py
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

REPORT = os.path.join("reports", "corr_gate_shadow.jsonl")


def load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def realized_pnl_by_position(tickets):
    """Return {position_id: net_pnl} for the given position ids, from MT5 history."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5-INIT-FAIL", mt5.last_error())
        return {}
    start = datetime.now(timezone.utc) - timedelta(days=120)
    end = datetime.now(timezone.utc) + timedelta(hours=2)
    deals = mt5.history_deals_get(start, end) or []
    want = set(int(t) for t in tickets if t)
    pnl = {}
    for d in deals:
        if d.position_id in want:
            pnl[d.position_id] = pnl.get(d.position_id, 0.0) + d.profit + d.commission + d.swap
    mt5.shutdown()
    return pnl


def main():
    rows = load_rows(REPORT)
    if not rows:
        print(f"no rows in {REPORT} yet — the shadow logs one row per inverse-mirror fire.")
        print("(needs a flat restart with corr_gate_shadow_enabled + a live EURUSD fill that mirrors.)")
        return

    tickets = []
    for r in rows:
        tickets += [r.get("lead_ticket"), r.get("follower_ticket")]
    pnl = realized_pnl_by_position(tickets)

    def leg(r, key):
        t = r.get(key)
        return pnl.get(int(t)) if t else None

    # attach realized P&L; keep only rows where BOTH legs have resolved (closed)
    resolved, pending = [], 0
    for r in rows:
        lp, fp = leg(r, "lead_ticket"), leg(r, "follower_ticket")
        if r.get("skipped"):  # follower never opened (already gated live) — lead only
            if lp is None:
                pending += 1; continue
            resolved.append((r, lp, 0.0, True))
        else:
            if lp is None or fp is None:
                pending += 1; continue
            resolved.append((r, lp, fp, False))

    corrs = [r["corr"] for r in rows if r.get("corr") is not None]
    n_skip = sum(1 for r in rows if r.get("verdict") == "skip")
    n_fire = sum(1 for r in rows if r.get("verdict") == "fire")
    print("=" * 68)
    print(f"CORR-GATE SHADOW  ({len(rows)} fires logged, {len(resolved)} fully resolved, {pending} pending)")
    thr = rows[-1].get("threshold", -0.70)
    print(f"threshold={thr}  window={rows[-1].get('window')}  tf={rows[-1].get('tf')}  "
          f"live_gate={rows[-1].get('live_gate')}")
    if corrs:
        print(f"corr at fire: mean={sum(corrs)/len(corrs):+.2f}  min={min(corrs):+.2f}  "
              f"max={max(corrs):+.2f}")
    print(f"verdicts: SKIP={n_skip}  FIRE={n_fire}")
    print("=" * 68)

    def bucket(rows_sub, label):
        if not rows_sub:
            print(f"\n{label}: (none)")
            return 0.0, 0.0
        lead_sum = sum(x[1] for x in rows_sub)
        foll_sum = sum(x[2] for x in rows_sub)
        comb = lead_sum + foll_sum
        print(f"\n{label}: n={len(rows_sub)}")
        print(f"  lead(EURUSD) net   {lead_sum:+9.2f}")
        print(f"  follower(USDJPY)   {foll_sum:+9.2f}")
        print(f"  combined (actual)  {comb:+9.2f}")
        return lead_sum, foll_sum

    skip_rows = [x for x in resolved if x[0].get("verdict") == "skip" and not x[3]]
    fire_rows = [x for x in resolved if x[0].get("verdict") == "fire" and not x[3]]
    _, skip_foll = bucket(skip_rows, "SKIP-verdict fires (LOCKSTEP — gate would DROP follower)")
    bucket(fire_rows, "FIRE-verdict fires (loose — gate KEEPS follower)")

    print("\n" + "-" * 68)
    print("VERDICT: arming the gate withholds the follower leg on SKIP rows.")
    saving = -skip_foll  # dropping the follower removes its P&L from the book
    print(f"  net P&L change from arming = -(follower P&L on SKIP rows) = {saving:+.2f}")
    if len(skip_rows) < 10:
        print("  (n<10 on the SKIP bucket — NOT decisive yet; keep collecting.)")
    elif saving > 0:
        print("  -> follower LOST on lockstep fires: the gate cuts double-losses. Candidate to arm.")
    else:
        print("  -> follower WON on lockstep fires: gating would cost money. Do NOT arm.")
    print("-" * 68)


if __name__ == "__main__":
    sys.exit(main())
