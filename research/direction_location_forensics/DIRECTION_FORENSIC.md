# DIRECTION_FORENSIC.md

> Measurement only — no strategy change proposed. Cut-points (fixed, not tuned): MFE-worked ≥0.5×stop, MFE-dead <0.15×stop, MAE-bad ≥0.6×stop, impulse ≥0.55, winner ≥1.0×stop, give-back capture <0.35. 'high' confidence = MFE/MAE present; 'proxy' = realized-only.


## Q1 — Was the faded direction wrong?

Direction grade from favorable excursion (MFE) vs the stop distance. `wrong` = price barely moved our way before failing; `right` = the fade had real edge at least intra-trade.


### LEAD — direction grade (high-confidence subset, n=63)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| right | 5 | 7.9% | 47.5 | 100.0 |
| weak | 21 | 33.3% | -2.9 | 90.5 |
| wrong | 37 | 58.7% | -190.5 | 21.6 |


_Full population n=313 (incl. proxy):_

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| right | 183 | 58.5% | 1341.9 | 100.0 |
| weak | 21 | 6.7% | -2.9 | 90.5 |
| wrong | 37 | 11.8% | -190.5 | 21.6 |
| unknown | 72 | 23.0% | -901.8 | 0.0 |



### NODE — direction grade (high-confidence subset, n=48)

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| right | 22 | 45.8% | 63.1 | 100.0 |
| unknown | 26 | 54.2% | -225.6 | 0.0 |


_Full population n=103 (incl. proxy):_

| grade | n | share | net_pips | win% |
|---|---|---|---|---|
| right | 66 | 64.1% | 476.2 | 100.0 |
| unknown | 37 | 35.9% | -489.6 | 0.0 |



**Reading:** a large `wrong` share would mean the entry side itself is the leak. A small `wrong` share with losses concentrated later (location/timing/exit) means the fade direction is broadly right and the damage is downstream.
