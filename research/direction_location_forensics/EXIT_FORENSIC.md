# EXIT_FORENSIC.md

> Measurement only — no strategy change proposed. Cut-points (fixed, not tuned): MFE-worked ≥0.5×stop, MFE-dead <0.15×stop, MAE-bad ≥0.6×stop, impulse ≥0.55, winner ≥1.0×stop, give-back capture <0.35. 'high' confidence = MFE/MAE present; 'proxy' = realized-only.


## Q4 — Right trade, bad exit?

Exit grade compares realized pips to the favorable excursion (MFE). `gave_back` = a winner-grade move (MFE ≥ stop) captured at <35%.


### LEAD — exit grade (high-confidence subset, n=63)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| ok | 63 | 100.0% | -145.9 | 50.8 |


_MFE capture (realized/MFE) over n=53: median 18%, min -1400%, max 100%._


### NODE — exit grade (high-confidence subset, n=48)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| unknown | 48 | 100.0% | -162.5 | 45.8 |


_MFE capture (realized/MFE) over n=43: median 7%, min -3300%, max 100%._


### LEAD — exit reasons

| reason | n |
|---|---|
| Stop Loss (SL) Hit | 159 |
| Trailing SL Hit | 88 |
| Take Profit (TP) Hit | 21 |
| Closed (Retest veto (veto)) | 19 |
| Closed (Manual) | 14 |
| Closed (EOD Flat (pre-rollover)) | 5 |
| Closed (EOD profit close) | 4 |
| Closed (EOD Hard Flat (2) | 1 |
| Closed (EOD Flat (pre-ro) | 1 |
| Closed (Retest veto (timeout)) | 1 |



### NODE — exit reasons

| reason | n |
|---|---|
| Trailing SL Hit | 42 |
| Stop Loss (SL) Hit | 36 |
| Closed (Retest veto (veto)) | 12 |
| Closed (EOD Flat (pre-rollover)) | 4 |
| Closed (Risk limit breach) | 4 |
| Take Profit (TP) Hit | 3 |
| Closed (Closed (Retest veto (veto))) | 1 |
| Closed (Retest veto (timeout)) | 1 |

