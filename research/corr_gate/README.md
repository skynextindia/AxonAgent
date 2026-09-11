# Correlation-gate shadow

**Problem (user, 2026-09-04):** the inverse mirror pairs EURUSD SELL with USDJPY BUY on
the assumption of a clean negative correlation. But when the two pairs are in **lockstep**
(rolling corr ≤ threshold) the legs are **one doubled bet** — they win together or **lose
together** (the double stop-out). Live 88-day study: corr swings −0.95..+0.27 (mean −0.58),
tight (≤−0.70) ~30% of the time — so there is signal to gate on, but true decoupling
(>−0.30) is only ~5%, so the mirror can never be relied on as a hedge. The gate's job is
**avoiding the doubling**, not adding diversification.

## How it works
On every inverse-mirror fire (`daemon.fire_inverse_mirror`, follower side):
1. `_pair_correlation(lead, follower, window, tf)` — rolling Pearson of the last `window`
   log-returns (default 24 × H1). Read-only history query.
2. `_corr_gate_eval` — verdict: `corr <= threshold` → **skip** (lockstep/doubling);
   else **fire**; missing corr → **fire** (fail-open, never block on a data gap).
3. **SHADOW** (`corr_gate_live=False`): fire the follower anyway, log the verdict + both
   tickets. **LIVE** (`corr_gate_live=True`): a **skip** verdict withholds the follower
   order entirely — the lead EURUSD leg is never touched.
4. One row per fire → `reports/corr_gate_shadow.jsonl`.

## Config (default_config.py)
| flag | default | meaning |
|---|---|---|
| `corr_gate_shadow_enabled` | `True` | log verdicts (measure only) |
| `corr_gate_live` | `False` | arm: skip verdict blocks the follower leg |
| `corr_gate_threshold` | `-0.70` | rolling corr ≤ this = lockstep → skip |
| `corr_gate_window` | `24` | rolling window (bars) |
| `corr_gate_tf` | `H1` | timeframe for the window |

## Reading it
`.venv/Scripts/python.exe research/corr_gate/corr_gate_report.py` (Eightcap terminal open).
Joins each leg's realized P&L from MT5 history by `position_id`, buckets combined P&L by
verdict. The gate withholds only the **follower** leg on SKIP rows, so:

> **net change from arming = −(follower P&L on SKIP rows)**
> follower lost on lockstep fires → gate cuts double-losses (candidate to arm);
> follower won → gating costs money (do not arm).

Needs n ≥ ~10 resolved SKIP rows before it's decisive.

## Status
Built read-only, staged behind the flags, **not armed**. Activates on the next flat
restart (shadow logging only). Decide arming from live rows, not in-sample.
Tests: `research/corr_gate/tests/test_corr_gate.py` (3/3).
