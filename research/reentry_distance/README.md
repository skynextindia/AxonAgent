# Re-entry distance

**Question (user, 2026-09-07):** the engine re-opened a EURUSD SELL **1.8 pips / 16 min**
from where it had just exited a SELL (09-07 09:14 @1.16119, right after the 08:58 @1.16137
exit) — and it lost the full −20p stop. The live engine gates re-entry **only** by a time
cooldown (15 min after a win / 45 after a loss); it never measures distance from its own
last exit. This module quantifies what that missing check would be worth — read-only,
decides nothing.

## What it measures
For every closed position, stamp the distance (pips) and gap (minutes) from the prior
**same-symbol, same-direction EXIT**, then bucket net P&L by "near re-entry" vs the rest.

- `reentry_core.py` — pure logic (no MT5): `annotate_reentries`, `is_near_reentry`,
  `summarize`. Unit-tested.
- `reentry_report.py` — pulls closed positions from MT5 history, runs the core, prints
  the buckets + a distance histogram.
- `tests/test_reentry.py` — 5/5 pass.

## Run
```
.venv/Scripts/python.exe research/reentry_distance/reentry_report.py --days 30 --pips 5 --min 60
```
(Eightcap terminal open. `--pips`/`--min` set the "near re-entry" thresholds.)

## Finding — 60d, 260 closed positions (2026-09-08, the robust read)
| bucket | n | net | avg | win% |
|---|---|---|---|---|
| NEAR re-entry (≤5p AND ≤60min) | 79 | **−$1027.76** | −13.01 | 60.8 |
| all other entries | 181 | +$111.40 | +0.62 | 59.1 |

Distance histogram (net, avg): 0–2p −492/−8.35 · 2–5p −337/−5.80 · **5–10p +127/+2.94** ·
10–20p −357/−10.19 · **20p+ +82/+1.35**. The closest (0–5p) buckets are clearly negative.

> **Note — the earlier 30d/99-position read was misleading.** It showed near re-entries
> losing *less* per trade (−$12.6 vs −$19.4), implying "no distance signal." The 60d sample
> **reverses** the per-trade sign (near −$13 vs rest +$0.6). Trust the 60d numbers.

**Verdict.** Near re-entries ARE the loss center (−$1028 vs +$111 breakeven for the rest) —
but they still **win 60.8%**; the damage is a **negative-skew tail** of catastrophic losses
(−$243, −$226, −$205, −$198, −$169, plus the −$105 doublings) — tiny clipped scalp wins
(+$2/+$3) against occasional full-size or doubled losses. So the cause is the
**clip-winner/full-loser exit asymmetry + doublings**, not closeness per se. A blanket
distance-skip would sacrifice a 60%-win stream to trim a few tail losses. → the higher-leverage
fixes are the good-spot selector (don't re-sell into an up-trend) + the corr-gate (don't
double in lockstep) + the exit asymmetry (small TP vs full SL), **not** a distance guard.

Read-only. Nothing wired to the daemon, nothing armed. See [[reentry-distance-measure]].
