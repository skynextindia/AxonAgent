# Autoresearch loop — entry-gate rework to true-entry signature (ARMED, DO NOT RUN)

Status: **ARMED**. Precondition NOT met. Separate from the exit-tuning loop — do
not merge; each isolates one hypothesis. Set up 2026-07-22 from the 132k-row
forward mining of 07-21 FX snapshots.

## Precondition (hard gate)
Do NOT start until >= 3 FX-only days confirm the h1_bias edge holds across regimes.
07-21 was net h1-bearish (baseline h1_bias -0.26), so the single-day edge may be
that day's trend, not a general signal. Confirm first, then tune.
Also: run AFTER the exit-tuning loop settles — exits are the proven leak; fixing
entries on top of unfixed exits confounds the metric.

## The finding (why this loop exists)
Forward mining of 132,748 FX rows. TRUE entry = clean >=3p run within 30 min,
<=2p adverse. 40,680 found (base rate 30.6%). Feature separation (sep = distance
of true-entry median from baseline, in base-IQR units):
- h1_bias: sep 0.58 DOMINANT. base -0.26, true-LONG +0.32, true-SHORT -0.38.
- range_pos: sep 0.28 secondary. LONG 0.61, SHORT 0.30.
- reversal_pressure / is_exhaustion_zone / tick_eff / disp_class(EXHAUSTION) /
  at_structure / regime: sep ~0.0 — NON-predictive. The current confluence gate
  keys on exactly these.

## TRUE ENTRY SIGNATURE (target — the necessary values)
- LONG:  h1_bias > ~+0.3  AND  range_pos > ~0.6
- SHORT: h1_bias < ~-0.3  AND  range_pos < ~0.3
Momentum / h1-aligned continuation, NOT reversal.

## Goal
Re-weight the entry confluence so it scores on the predictive features
(h1_bias alignment, range_pos) and stops crediting the non-predictive ones
(reversal_pressure==1.0, EXHAUSTION disp, tick_eff climax). Make h1_bias
alignment a necessary condition (reject counter-h1 entries) — validate as a gate,
do not hard-code off one day.

## Scope
- axonai/realtime/reversal_model.py (_unified_confluence_score components + the
  line ~234 full-credit-for-counter-h1 branch)
- axonai/default_config.py (entry_* / confluence weight keys only)
Forbidden: exit_engine.py (that is the other loop), SIGNAL_QUALITY_BY_SYMBOL
thresholds until the exit loop is done.

## Metric / Verify / Guard
- Metric: FX net-pips expectancy from offline backtester (same as exit loop).
- Verify: backtester replay over FX snapshots -> net pips/trade (pin exact CLI
  before first run; do not guess).
- Guard: `python -m pytest tests/ -q` green.

## Related open item (no data needed)
EUR floor 0.50 is the loosest FX floor and logged ZERO confluence rejections on
07-21 (every armed EUR setup fired -> 6 of 10 trades, led the -7p). USDJPY 0.60
floor blocked 620/688 (protective; JPY trended). Revisit raising EUR's floor
toward 0.55 once multi-day data lands. See memory: axonai-ideal-entry-signature.
