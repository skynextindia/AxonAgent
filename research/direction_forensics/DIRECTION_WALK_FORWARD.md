# DIRECTION_WALK_FORWARD.md

> READ-ONLY / SHADOW. No production change is proposed or made. rank-AUC 0.5 = no separation; distance from 0.5 = discriminating power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE and exist only on a recent, August-concentrated subset; 'coarse' labels (WIN vs LOSS) span the full history.


## Chronological validation — fit on TRAIN only, freeze, evaluate held-out

TRAIN 2026-06-15..07-15 · VALIDATION 07-16..07-31 · OOS 08-01..08-18. The threshold grid is searched ONLY on TRAIN; the single best policy is applied unchanged to VALIDATION and OOS.


**Best TRAIN policy:** `g18[disp>=0.45; votes>=1]`


| slice | n | kept | vetoed | base_R | kept_R | ΔR | base_PF | kept_PF | veto_wrongdir | veto_winners |
|---|---|---|---|---|---|---|---|---|---|---|
| TRAIN | 114 | 114 | 0 | 6.225 | 6.225 | 0.0 | 1.139 | 1.139 | 0 | 0 |
| VALIDATION | 88 | 88 | 0 | 9.775 | 9.775 | 0.0 | 1.777 | 1.777 | 0 | 0 |
| OOS (Aug) | 111 | 110 | 1 | -5.319 | -5.224 | 0.095 | 0.698 | 0.702 | 1 | 0 |


### Feature coverage by split (why the above looks the way it does)

| slice | n | MFE labels | displacement_ratio | sr_level_dist_pips | regime_confidence |
|---|---|---|---|---|---|
| train | 114 | 0 | 0 | 0 | 113 |
| validation | 88 | 0 | 0 | 0 | 88 |
| oos | 111 | 63 | 61 | 111 | 111 |


**Generalizes across held-out slices:** False

**Fitted-to-August (helps OOS, hurts validation):** False

**Untrainable (discriminating features absent in TRAIN):** True

**Walk-forward verdict:** best TRAIN policy vetoes 0 trades on TRAIN+VALIDATION — it is effectively inaction (the discriminating features are absent before August). No filter to validate; its only vetoes fall in OOS/August, i.e. in-sample to the adverse regime.


> Any rule that only improves OOS/August while hurting VALIDATION is rejected by design — it is fitted to the single adverse regime, not a stable direction signal.
