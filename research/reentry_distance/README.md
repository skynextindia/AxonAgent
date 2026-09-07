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

## Finding (30d, 99 closed positions, 2026-09-07)
| bucket | n | net | avg | win% |
|---|---|---|---|---|
| NEAR re-entry (≤5p AND ≤60min) | 31 | −$390.54 | −12.60 | **51.6** |
| all other entries | 68 | −$1321.68 | −19.44 | 36.8 |

Distance histogram (net, avg): 0–2p −377/−19.8 · 2–5p −427/−15.2 · 5–10p −233/−18.0 ·
**10–20p −521/−40.1** · 20p+ −17/−0.8.

**Verdict — a distance-only re-entry guard is NOT justified.** Near re-entries actually
*win more often* (51.6% vs 36.8%) and lose *less per trade* than the average entry, so
skipping them would drop winners and losers roughly evenly. Distance-from-exit is not
monotonic (the *worst* bucket is 10–20p, and 20p+ is ~breakeven). The account damage
concentrates in a handful of full-stop **doublings** (09-02, 09-03 14:06, 09-04 15:58,
09-07 09:14 — each ≈ −$105, ≈ −$420 total), which correlate with **trend/direction**, not
with how close the re-entry was. → the fix is the good-spot selector (don't re-sell into an
up-trend) + the corr-gate (don't double in lockstep), **not** a distance guard.

Read-only. Nothing wired to the daemon, nothing armed. See [[reentry-distance-measure]].
