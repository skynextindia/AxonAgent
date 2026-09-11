# CONTINUATION_VS_REVERSAL.md

> READ-ONLY / SHADOW. No production change is proposed or made. rank-AUC 0.5 = no separation; distance from 0.5 = discriminating power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE and exist only on a recent, August-concentrated subset; 'coarse' labels (WIN vs LOSS) span the full history.


## The hypothesis, mapped to what the telemetry actually holds

The brief's reversal/continuation signatures require these inputs. Several are NOT in the logs — stated plainly rather than approximated away.

| hypothesis component | mapped feature | availability |
|---|---|---|
| extreme_velocity | velocity_divergence | present (static snapshot) |
| velocity_decay | — | UNAVAILABLE — logs a single velocity value, not its trajectory |
| deteriorating_efficiency | tick_efficiency | present (static snapshot; low=deteriorating) |
| reversal_pressure | divergence_warning | proxy only (boolean warning + velocity_divergence) |
| exhaustion_flag | peak_type | partial (peak_type contains 'exhaustion'; regime er_exhaustion on 23 rows) |
| continued_acceleration | — | UNAVAILABLE — no velocity trajectory in logs |
| strong_displacement | displacement_ratio | present on 72 rows only |
| persistent_efficiency | tick_efficiency | present (static snapshot; high=persistent) |
| directional_liquidity_break | breakout_fresh_extreme | proxy only, 64 rows (breakout_shadow) |


## Test of the discriminating components (fine subset)

| component→feature | AUC (clean_win vs wrong_dir) | verdict |
|---|---|---|
| extreme_velocity→velocity_divergence | 0.458 | no separation (≈chance) |
| deteriorating_efficiency→tick_efficiency | 0.409 | no separation (≈chance) |
| strong_displacement→displacement_ratio | 0.433 | no separation (≈chance) |
| persistent_efficiency→tick_efficiency | 0.409 | no separation (≈chance) |


**Key limitation:** velocity *decay* and *continued acceleration* — the temporal core of the hypothesis — cannot be measured: the journal logs a single static `velocity_divergence` per trade, not its trajectory. The reversal-vs-continuation distinction the hypothesis proposes is therefore only partially testable from existing telemetry.


**Result:** None of the testable hypothesis components separate winning fades from run-over fades (all AUC ≈ 0.5). The proposed reversal-vs-continuation signature is NOT present in the existing telemetry.
