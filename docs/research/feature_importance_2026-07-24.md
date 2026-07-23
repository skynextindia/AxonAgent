# Feature Importance — signal-level (2026-07-24)

Project research note. Mutable; supersede as new data arrives (especially after
the `run.py` restart onto the corrected entry engine). Not a stable fact — do not
promote to persistent memory.

## Question
Do the continuous confluence inputs (velocity, decay, reversal_pressure, biases,
room, dist_to_sr, ...) carry standalone predictive signal, or is the confluence
engine stacking redundant variables?

## Method (signal-level, unconditional, pre-restart valid)
Same falsification method that killed `decay_ratio` and `reversal_pressure`,
extended to all 32 logged numeric features. On the engine snapshots
(`reports/engine_snapshots_{SYM}.csv`, old engine), pooled 4 FX, 809k tick→outcome
pairs. Target = reversal return:

    tgt = -sign(net_disp_pips) * (price[t+H] - price[t]) / pip

Positive = price reverted after the recent displacement (a fade would have paid).
Rank each feature by Spearman(feature, tgt) at H = 15/30/60 min; report top-vs-
bottom quintile spread in pips against the ~1p cost line. Script:
`scratchpad/feature_importance.py` (method), reproducible from the snapshots.

This is UNCONDITIONAL (every tick), NOT trade-conditional (at fills). It is a
floor: it cannot see interaction or conditional edges.

## Result — continuous features are individually dead
Every one of the 32 numeric features: |Spearman rho| <= 0.030 at 30 min, quintile
spread < 0.4p (all well under the ~1p cost line). Strongest was `net_disp_pips`
(rho 0.030, 0.61p) but that is the conditioning variable (semi-mechanical). No
continuous feature is a standalone edge. Confirms and extends the decay /
reversal_pressure falsification to the whole continuous stack.

Implication: a weighted linear confluence sum of near-zero-signal components is
unlikely to produce a strong signal unless the interaction is modelled explicitly.

## Result — the separation is categorical / state
| dimension | best pocket | worst pocket |
|---|---|---|
| disp_class | IMPULSE +0.63p (n=40k) | TRAP +0.08p |
| entry_state | RETEST_WAIT +0.52p (n=7.8k) | TRIGGERED -0.05p (n=10.7k) |
| regime | TREND_CONTINUATION +0.28p | TREND_EXPANSION -0.62p, COMPRESSION -0.18p |
| near_level_type | support +0.19p | resistance +0.14p (near-symmetric) |

Even the best pockets are sub-cost univariately. Two observations stand out:
- `RETEST_WAIT +0.52p` vs `TRIGGERED -0.05p`: the edge sat one state *before* the
  old engine actually fired. Consistent with "confirmation arrives too late /
  opportunity decayed / execution latency." The S/R-anchored retest rework targets
  exactly this; re-check post-restart.
- `TREND_EXPANSION` and `COMPRESSION` are negative — avoidance signals, often more
  robust than entry signals.

## Critical caveat — this understates conditional features
Signal-level is blind to conditional/interaction edges, provable from this run:
`room_pips` scored rho -0.006 / -0.06p here (≈ nothing), yet at actual fills the
room veto flipped a sample -4.2p -> +19.9p. Room's edge is conditional on being in
a fade setup, invisible when averaged over all ticks. Same reason the chart-pattern
edge (geometric/sequential) does not appear. So: do NOT delete room or velocity on
this evidence alone.

## Longer-horizon note
`active_sweeps` (rho 0.019) and `structure_break` (0.016) strengthen at H=60m while
~0 at 30m — a longer-hold signal, consistent with the unconfirmed active_sweeps
lead. Not a 30-min feature.

## Decisions taken
1. Do NOT simplify the confluence weights yet. Sequence: restart -> collect
   500-1000 new-engine fills -> re-run BOTH signal-level and trade-conditional
   importance -> remove only features that fail in BOTH (avoids deleting variables
   whose value only appears inside valid setups).
2. Two regime-avoidance vetoes built behind a default-off flag
   `entry_avoid_regimes: []` (candidate `["TREND_EXPANSION", "COMPRESSION"]`) in
   `axonai/realtime/reversal_model.py` `_unified_confluence_score`. OFF until a
   restarted session confirms these regimes stay negative under the new engine.
3. Validity: snapshots are OLD-engine output. Continuous-feature and regime
   findings are engine-independent; the `entry_state` findings (RETEST_WAIT /
   TRIGGERED) will shift after restart and must be re-run.

## Theme
The system's value appears to come from state transitions and conditional
structure, not individual continuous indicators. Future research effort belongs in
state sequencing and interaction modelling, not additional standalone features.
