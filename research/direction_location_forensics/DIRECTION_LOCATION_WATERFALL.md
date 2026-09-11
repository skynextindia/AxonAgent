# DIRECTION_LOCATION_WATERFALL.md

> Measurement only — no strategy change proposed. Cut-points (fixed, not tuned): MFE-worked ≥0.5×stop, MFE-dead <0.15×stop, MAE-bad ≥0.6×stop, impulse ≥0.55, winner ≥1.0×stop, give-back capture <0.35. 'high' confidence = MFE/MAE present; 'proxy' = realized-only.


## The five-stage attribution

Each trade is attributed to the FIRST failing stage: **direction → location → timing → exit → risk-state**. Winners with a clean exit are `clean_win`. Losses with no MFE/MAE are `loss_no_excursion_data` (kept separate — never counted as a measured cause).


### LEAD — waterfall, FULL population (n=313, net 246.7p, win 67.5%)

| stage | n | share | net_pips | win% |
|---|---|---|---|---|
| clean_win | 212 | 67.7% | 1378.7 | 100.0 |
| wrong_direction | 29 | 9.3% | -198.3 | 0.0 |
| bad_location | 1 | 0.3% | -30.3 | 0.0 |
| bad_timing | 14 | 4.5% | -164.0 | 0.0 |
| risk_state_distortion | 6 | 1.9% | -30.6 | 0.0 |
| loss_no_excursion_data | 51 | 16.3% | -708.8 | 0.0 |


### LEAD — waterfall, HIGH-CONFIDENCE subset (MFE/MAE present, n=63)

| stage | n | share | net_pips | win% |
|---|---|---|---|---|
| clean_win | 32 | 50.8% | 84.3 | 100.0 |
| wrong_direction | 29 | 46.0% | -198.3 | 0.0 |
| bad_location | 1 | 1.6% | -30.3 | 0.0 |
| risk_state_distortion | 1 | 1.6% | -1.6 | 0.0 |


**Risk-state effects present (any stage):** manual×14, eod_flat×11, retest_veto×20




### NODE — waterfall, FULL population (n=103, net -13.4p, win 64.1%)

| stage | n | share | net_pips | win% |
|---|---|---|---|---|
| clean_win | 66 | 64.1% | 476.2 | 100.0 |
| risk_state_distortion | 20 | 19.4% | -119.2 | 0.0 |
| loss_no_excursion_data | 10 | 9.7% | -249.1 | 0.0 |
| unclassified_loss | 7 | 6.8% | -121.3 | 0.0 |


### NODE — waterfall, HIGH-CONFIDENCE subset (MFE/MAE present, n=48)

| stage | n | share | net_pips | win% |
|---|---|---|---|---|
| clean_win | 22 | 45.8% | 63.1 | 100.0 |
| risk_state_distortion | 19 | 39.6% | -104.3 | 0.0 |
| unclassified_loss | 7 | 14.6% | -121.3 | 0.0 |


**Risk-state effects present (any stage):** eod_flat×4, riskguard_breach×4, retest_veto×14




## How to read this
- A dominant `wrong_direction` bar ⇒ the entry side is the leak (fix direction).
- A dominant `bad_location`/`bad_timing` bar ⇒ direction is right; the entry trigger/context is the leak.
- A dominant `bad_exit` bar ⇒ the setups are right; the trade management gives it back.
- `risk_state_distortion` quantifies losses driven by forced exits (RiskGuard breach / EOD flat / retest veto), not the setup itself.

_No fix is recommended here by design — this file localizes WHERE the money leaks so the next phase can target it._
