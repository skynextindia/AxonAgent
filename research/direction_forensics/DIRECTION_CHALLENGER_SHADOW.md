# DIRECTION_CHALLENGER_SHADOW.md

> READ-ONLY / SHADOW. No production change is proposed or made. rank-AUC 0.5 = no separation; distance from 0.5 = discriminating power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE and exist only on a recent, August-concentrated subset; 'coarse' labels (WIN vs LOSS) span the full history.


## Pure shadow challenger (veto-only; never reaches the executor)

Input = decision-point features. Output = BUY/SELL/HOLD + reason codes. It keeps the production fade unless a continuation signature fires, then HOLDs. It can only REMOVE trades — never add or flip one.


### Best full-history policy: `g18[disp>=0.45; votes>=1]`

| metric | baseline (keep all) | challenger (kept) |
|---|---|---|
| trades | 313 | 312 |
| net R | 10.681 | 10.776 |
| profit factor | 1.143 | 1.144 |
| win rate % | 67.5 | 67.7 |
| max drawdown R | -8.633 | -8.633 |


### Rejection accounting

| quantity | value |
|---|---|
| trades vetoed | 1 |
|   of which wrong_direction (good vetoes) | 1 |
|   of which clean winners (FALSE vetoes) | 0 |
|   of which other losses | 0 |
|   of which unlabeled | 0 |
| losses avoided (R) | 0.095 |
| winners forgone (R) | 0.0 |
| net R retained % | 100.9 |
| trade-count change | -1 |


### By symbol

| symbol | kept | vetoed | kept_net_R | vetoed_wrong_dir | vetoed_winners |
|---|---|---|---|---|---|
| EURUSD | 229 | 1 | 11.95 | 1 | 0 |
| USDJPY | 83 | 0 | -1.18 | 0 | 0 |


### By direction

| direction | kept | vetoed | kept_net_R | vetoed_wrong_dir | vetoed_winners |
|---|---|---|---|---|---|
| SELL | 195 | 1 | 16.1 | 1 | 0 |
| BUY | 117 | 0 | -5.32 | 0 | 0 |


### By month

| month | kept | vetoed | kept_net_R | vetoed_wrong_dir | vetoed_winners |
|---|---|---|---|---|---|
| 2026-06 | 57 | 0 | 6.18 | 0 | 0 |
| 2026-07 | 145 | 0 | 9.82 | 0 | 0 |
| 2026-08 | 110 | 1 | -5.22 | 1 | 0 |


### By regime

| regime | kept | vetoed | kept_net_R | vetoed_wrong_dir | vetoed_winners |
|---|---|---|---|---|---|
| unknown | 1 | 0 | -0.68 | 0 | 0 |
| ranging | 168 | 0 | 4.93 | 0 | 0 |
| breakout | 15 | 0 | 2.28 | 0 | 0 |
| panic | 46 | 0 | 0.35 | 0 | 0 |
| compression | 82 | 1 | 3.9 | 1 | 0 |

