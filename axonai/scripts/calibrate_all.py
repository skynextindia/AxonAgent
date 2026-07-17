#!/usr/bin/env python3
"""Daily calibration runner for ALL traded symbols.

Runs the EOD reversal analysis for every pair with a per-symbol auto-scaled
reversal threshold, regenerating reports/calibration_params_{symbol}.json that
each AxonDaemon loads at startup. A pair with too few reversals that day keeps
its config defaults (safe no-op), so this never degrades a pair on a quiet day.

Schedule this once per day (Windows Task Scheduler / cron), e.g. after the
session rolls over, then restart the daemons (or let them reload on next start):

    python -m axonai.scripts.calibrate_all
"""

from __future__ import annotations

from axonai.scripts.eod_reversal_analysis import analyze, _default_reversal_pips
from axonai.scripts.range_stats import compute as compute_range_stats

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD"]


def main() -> None:
    for sym in SYMBOLS:
        rev_pips = _default_reversal_pips(sym)
        print(f"[calibrate_all] {sym} @ {rev_pips} pips")
        try:
            analyze(sym, reversal_pips=rev_pips, pre=10, min_events=8)
        except Exception as e:  # never let one pair abort the batch
            print(f"[calibrate_all] {sym} FAILED: {e}")
        try:
            compute_range_stats(sym)  # daily range / ADR / reversal-zone stats
        except Exception as e:
            print(f"[calibrate_all] {sym} range_stats FAILED: {e}")


if __name__ == "__main__":
    main()
