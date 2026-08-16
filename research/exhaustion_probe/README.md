# Exhaustion-state grading probe (2026-08-17)

One-off research probes that tested whether the regime map's proposed **5th
"exhaustion" state** (wide-range + low-ER at an extreme) would actually *grade*
our entries — i.e. does exhaustion depth/quality split outcome? **Verdict: NO-GO.**
The 5th state was NOT built; the regime map stays a shadow labeler.

## Scripts (run order)

1. **`exhaustion_grade_probe.py`** — reconstruction on 301 real entries
   (join `peak_detection.trade_result.order == trade_closed.ticket`; exhaustion
   features rebuilt from real M15 bars strictly-before `trigger_candle.open_time`).
   Grades ER20/ER60, range width, extremeness, velocity_divergence, ppte, and a
   composite against **realized pips**, 20k-shuffle null, combined + per symbol.
   Result: no split on combined/EURUSD (composite even wrong-signed); only a
   fragile USDJPY low-ER flicker (1-of-21 tests → noise).

2. **`exhaustion_mfe_probe.py`** — MFE tiebreaker v1 (grade by max favorable
   excursion to decouple entry quality from the exit). **Superseded** — price-based
   fill anchoring covered only 108/301 and failed its sanity check
   (`corr(MFE, pips) ≈ 0`). Kept for provenance.

3. **`exhaustion_mfe_probe2.py`** — MFE tiebreaker v2 (the one that stands).
   Bar-close anchor (all 301/301), and grades the **volatility-neutral** ratio
   `favret = MFE/(MFE+MAE)` so a wide-range setup can't win just by being volatile.
   Result: raw-MFE hits (`extreme`, `width_atr`) are volatility artifacts that
   vanish under `favret` — nothing significant on any group; `er20` points
   anti-thesis; the USDJPY flicker is not corroborated. Confirms the NO-GO from
   the entry-quality side.

## Caveats / reproducibility

- **Data lives outside the repo.** These read `reports/signals.jsonl` (live log)
  and cached 90-day tick/bar caches `realtick90_{EURUSD,USDJPY}.npz` that were
  pulled into a scratchpad dir (`SCRATCH` const at the top of each script). To
  re-run, repoint `SCRATCH`/`LOG` and regenerate the `.npz` caches.
- **Read-only.** None of these place, modify, or close any order.
- Full write-up and the numbers are in the `regime-map-phase1` project memo.
