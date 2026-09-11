# DIRECTION_FEATURE_FORENSIC.md

> READ-ONLY / SHADOW. No production change is proposed or made. rank-AUC 0.5 = no separation; distance from 0.5 = discriminating power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE and exist only on a recent, August-concentrated subset; 'coarse' labels (WIN vs LOSS) span the full history.


## Decision-point feature separation: clean winners vs wrong-direction losses

Fair comparison on the MFE-labeled subset (clean_win n=32, wrong_direction n=29). Features ranked by discriminating power (|AUC−0.5|). CI = deterministic bootstrap 95%.

| feature | AUC | power | 95% CI | CI excl 0.5? | n_pos | n_neg |
|---|---|---|---|---|---|---|
| tick_efficiency | 0.409 | 0.091 | [0.254,0.55] | no | 32 | 29 |
| sr_level_dist_pips | 0.573 | 0.073 | [0.423,0.714] | no | 32 | 29 |
| displacement_ratio | 0.433 | 0.067 | [0.268,0.575] | no | 31 | 28 |
| regime_confidence | 0.436 | 0.064 | [0.305,0.576] | no | 32 | 29 |
| velocity_divergence | 0.458 | 0.042 | [0.316,0.596] | no | 32 | 29 |
| spread_pips | 0.46 | 0.04 | [0.315,0.614] | no | 32 | 29 |
| peak_confidence | 0.502 | 0.002 | [0.378,0.621] | no | 32 | 29 |


## Same features on the full history (coarse WIN vs LOSS)

| feature | AUC | power | 95% CI | CI excl 0.5? | n_pos | n_neg |
|---|---|---|---|---|---|---|
| displacement_ratio | 0.409 | 0.091 | [0.274,0.566] | no | 31 | 30 |
| sr_level_dist_pips | 0.542 | 0.042 | [0.425,0.642] | no | 72 | 39 |
| regime_confidence | 0.475 | 0.025 | [0.413,0.541] | no | 210 | 100 |
| peak_confidence | 0.479 | 0.021 | [0.432,0.529] | no | 210 | 100 |
| tick_efficiency | 0.481 | 0.019 | [0.399,0.55] | no | 210 | 100 |
| spread_pips | 0.488 | 0.012 | [0.42,0.547] | no | 210 | 100 |
| velocity_divergence | 0.509 | 0.009 | [0.442,0.577] | no | 210 | 100 |


## Categorical features


**peak_type** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| microstructure_exhaustion | 31 | 28 |
| velocity_exhaustion | 1 | 1 |



**mtf_alignment** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| NEUTRAL | 23 | 24 |
| BULLISH | 9 | 5 |



**intensity** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| None | 32 | 29 |



**divergence_warning** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| None | 32 | 29 |



**sr_level_type** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| M15_SWING | 8 | 7 |
| LNDH | 3 | 6 |
| TODAY_H | 2 | 4 |
| ASH | 4 | 1 |
| PDL | 2 | 2 |
| ROUND | 3 | 1 |
| TODAY_L | 3 | 1 |
| H4_SWING | 2 | 1 |
| ASL | 1 | 2 |
| LNDL | 2 | 1 |
| NYH | 0 | 2 |
| NYL | 0 | 1 |
| PDH | 1 | 0 |
| PWH | 1 | 0 |



**structure_state** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| with_structure | 8 | 10 |
| range | 7 | 10 |
| against_structure | 9 | 5 |
| None | 8 | 4 |



**dominant_regime** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| ranging | 18 | 16 |
| compression | 13 | 10 |
| panic | 0 | 3 |
| breakout | 1 | 0 |



**daily_trend** (pos=clean_win, neg=wrong_direction):

| value | clean_win | wrong_dir |
|---|---|---|
| sideways | 32 | 29 |



**Reading:** an AUC whose CI excludes 0.5 is a real (if weak) separator. If every CI straddles 0.5, the decision-point features do not distinguish a winning fade from a run-over fade.
