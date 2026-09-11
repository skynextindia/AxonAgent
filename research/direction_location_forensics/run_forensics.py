"""CLI orchestrator for the direction/location forensics. READ-ONLY.

    python -m research.direction_location_forensics.run_forensics

Loads lead+node history, classifies every trade, writes datasets under out/ and
the five markdown reports in the package root, and prints a compact summary.
"""

from __future__ import annotations

import json

from .loader import load_all
from .classify import classify_all
from .report import write_datasets, write_markdown, summary


def main() -> int:
    all_t = load_all()
    all_v = {acct: classify_all(ts) for acct, ts in all_t.items()}

    flat_t = [t for ts in all_t.values() for t in ts]
    flat_v = [v for vs in all_v.values() for v in vs]

    paths = write_datasets(flat_t, flat_v)
    summ = summary(all_v)
    md = write_markdown(all_t, all_v, summ)

    # persist the machine-readable summary too
    import os
    from .report import OUT_DIR, _assert_isolated
    sp = os.path.join(OUT_DIR, "summary.json")
    _assert_isolated(sp)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summ, f, indent=1)

    print("=== Direction/Location Forensics ===")
    for acct in ("lead", "node"):
        s = summ.get(acct, {})
        print(f"\n[{acct}] n={s.get('n_trades')} net={s.get('net_pips')}p "
              f"win={s.get('win_rate')}% (high-confidence MFE/MAE rows: {s.get('n_high_confidence')})")
        print("  waterfall (full population):")
        for r in s.get("waterfall_all", []):
            print(f"    {r['bucket']:24s} n={r['n']:3d} ({r['pct']:4.1f}%)  net={r['net_pips']:8.1f}p  win={r['win_rate']}")
        if s.get("risk_state_effects"):
            print("  risk-state effects:", s["risk_state_effects"])
    print("\nDatasets:", ", ".join(paths.values()))
    print("Reports :", ", ".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
