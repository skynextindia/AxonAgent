# LOCATION_FORENSIC.md

> Measurement only — no strategy change proposed. Cut-points (fixed, not tuned): MFE-worked ≥0.5×stop, MFE-dead <0.15×stop, MAE-bad ≥0.6×stop, impulse ≥0.55, winner ≥1.0×stop, give-back capture <0.35. 'high' confidence = MFE/MAE present; 'proxy' = realized-only.


## Q2 — Right direction, wrong location?

Location grade from adverse excursion (MAE) depth vs stop, plus S/R-side context. `bad` = the trade ran deep against us early (poor entry spot) even when the direction eventually worked.


### LEAD — location grade (high-confidence subset, n=63)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| ok | 58 | 92.1% | -4.1 | 55.2 |
| bad | 5 | 7.9% | -141.8 | 0.0 |



### NODE — location grade (high-confidence subset, n=48)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| unknown | 48 | 100.0% | -162.5 | 45.8 |



### LEAD — by S/R level type at entry (context; join rate is partial)

| sr_level_type | n | net_pips | win% |
|---|---|---|---|
| M15_SWING | 34 | -28.4 | 70.6 |
| LNDH | 16 | 34.8 | 62.5 |
| TODAY_H | 12 | -43.7 | 58.3 |
| ROUND | 11 | 1.3 | 63.6 |
| NYH | 5 | -10.6 | 40.0 |
| LNDL | 5 | -39.8 | 60.0 |
| TODAY_L | 5 | 11.1 | 80.0 |
| PDL | 5 | -33.1 | 40.0 |
| ASH | 5 | 1.7 | 80.0 |
| PDH | 3 | 15.2 | 100.0 |
| ASL | 3 | -25.1 | 33.3 |
| H4_SWING | 3 | -0.6 | 66.7 |
| NYL | 2 | -2.2 | 50.0 |
| PWL | 1 | 2.6 | 100.0 |
| PWH | 1 | 0.9 | 100.0 |

