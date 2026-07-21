# Autoresearch loop — exit-gate tuning (ARMED, DO NOT RUN YET)

Status: **ARMED**. Precondition NOT met. First iteration must not run until the
data guard below passes. Set up 2026-07-22 from the 07-21 FX deep-scan findings.

## Precondition (hard gate)
Do NOT start iterations until **>= 3 FX-only trading days** of clean data exist,
each with the execution telemetry committed in 07a598a (spread / slippage /
profit_protect_pips_ref). One in-sample chop day (07-21) overfits — the forward
test already refuted a hindsight entry hypothesis on exactly that trap.

Check: count distinct dates in reports/trade_analytics.jsonl for FX symbols
(EURUSD/GBPUSD/USDJPY/AUDUSD) with entry_fill_price populated (proves telemetry
build was live). Need >= 3.

## Goal
Raise FX-only net-pips expectancy by reworking the EXIT gates so momentum-aligned
trades stop round-tripping. Entries are NOT in scope — the forward test showed
range_pos is momentum, not reversal, and the entries are on the winning side; the
leak is exits (adverse_impulse cutting continuation).

## Scope (files the loop may edit)
- axonai/realtime/exit_engine.py
- axonai/default_config.py  (exit_* keys ONLY: exit_profit_protect_pips,
  adverse_impulse_min_ticks, exit_engine_enable_exhaustion, exhaustion_* keys)

Forbidden: any entry-path file (entry_state_machine.py, reversal_model.py entry
gate, SIGNAL_QUALITY_BY_SYMBOL, daemon entry logic). Changing entries invalidates
the exit-only hypothesis.

## Metric
Net pips per trade (expectancy) from the OFFLINE backtester replayed over the
accumulated FX snapshot set. Direction: higher_is_better.
Secondary (report, not optimize): MFE-capture ratio, round-trip rate, max drawdown.

## Verify
Backtester over reports/engine_snapshots_*.csv (FX only) -> print net pips/trade
as a single number. NOTE: the exact backtester replay invocation must be confirmed
against backtester.py's API and pinned here before the first run (do not guess it).

## Guard (must always pass)
`python -m pytest tests/ -q`  — full suite green. A tuning change that reds the
suite is reverted regardless of metric. This is a live-trading branch.

## First experiment (highest leverage)
Make the cut-gate protection floor relative to each trade's own MFE/ATR instead of
the fixed `4.0 * clamp(vol_pips,0.5,3.0)`. On 07-21 that floor (~3.6p EUR, 12p on a
vol spike) sat above the day's MFE (~2.6p median), so trades never reached the
trailing manager and died inside the cut gates. This one change gates all three
cutters at once. See memory: axonai-exit-engine-diagnosis.

## Ordering
Exit tuning first (this loop). Gold re-enable stays LAST, only after FX is
validated — clear DISABLED_SYMBOLS + re-add XAUUSD to launchers. Do not fold gold
into this loop.
