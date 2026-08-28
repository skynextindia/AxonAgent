# RISK_ENGINE_SIMULATION_PLAN.md

Plan for the read-only historical risk-policy simulator. SHADOW / RESEARCH ONLY.
No live wiring. No automatic parameter optimization. No survival verdict claimed.

## 1. Data

- **Source (read-only):** `reports/signals_node.jsonl` (node, 103 `trade_closed`,
  2026-07-30→08-18) and `reports/signals.jsonl` (lead, 313, 2026-06-15→08-18).
- **Per-trade reconstruction** (schema has no stop/equity/atr fields):
  - `pip_value` — **empirical** from the row: `|profit| / (|pips| × volume)`
    (exact; e.g. USDJPY ≈ $6.3, EURUSD $10). Fallback = production formula.
  - `stop_pips` — reconstructed hard-distance **20 (EURUSD) / 30 (USDJPY)**.
  - `entry_ts = close_ts − hold_seconds`; events sorted in time order.
- **Nothing is written back to the journals.**

## 2. Replay model (event-driven)

Entry and exit events are interleaved in time order:

- **ENTRY** → build `RiskState` from the running (realized) equity, cushion to
  both floors, current daily-loss %, and the currently-open position set; call
  `decide()`; record the hypothetical lot + risk. No equity change at entry.
- **EXIT** → realize `hypo_pnl = pips × pip_value × hypo_lot`; update equity,
  drawdown, floor checks, per-day loss.

This **isolates the sizing effect** by holding the realized price path (`pips`)
fixed. It is a counterfactual "what if the same trades were sized by policy P".

## 3. Policies (A–F) — illustrative, params external, NOT recommendations

| ID | Policy | Mechanism |
|----|--------|-----------|
| A | `fixed_1100usd` | current node $-budget (equity-invariant) |
| B | `fixed_pct` | equity-proportional at the current lead 1.1% (reference only) |
| C | `drawdown_scaled` | pct + floor taper keyed to cushion shrink |
| D | `floor_aware` | pct + tighter floor taper + projected-breach block |
| E | `correlation_aware` | pct + shared-USD-unit (halve correlated leg) |
| F | `floor_plus_corr` | D + E combined |

The `0.011` base and all taper/cap params are **bench settings for comparison**,
explicitly not validated thresholds. The simulator runs this fixed set; it does
**not** search or optimize.

## 4. Metrics (per policy, + month / symbol / direction / regime breakdown)

final equity · max drawdown ($/%) · min equity · buffered-floor breaches ·
firm-floor breaches · daily-loss events · forced-flatten equivalents (proxy) ·
rejected trades · reduced-size trades · gross profit/loss · profit factor ·
expectancy · win rate · winner exposure ($ risk on winners) · avg risk/trade ·
max correlated exposure (concurrent gross USD notional) · max daily loss ($/%).

Regime slices reuse the prior boundaries: R1_Jun, R2_earlyJul, R3_lateJul_ramp,
R4_Aug_bleed.

## 5. Illustrative run (node journal, 2026-08-18) — infrastructure check, not a result

```
policy                 final_eq  maxDD%  bufBrch  firmBrch    PF  avgRisk%
A_fixed_1100usd           98435    8.76        0         0  0.93     1.06
B_fixed_pct               98128    9.09        0         0  0.91     1.10
C_drawdown_scaled         97947    8.29        0         0  0.90     1.01
D_floor_aware             98144    9.07        0         0  0.92     1.10
E_correlation_aware       99483    7.83        0         0  0.97     1.05
F_floor_plus_corr         99483    7.83        0         0  0.97     1.05
```

Baseline breakdown: by month 2026-07 **+4,618** / 2026-08 **−6,183**; by symbol
USDJPY **+1,641** / EURUSD **−3,206**; win rate 64%, PF 0.93 — the ramp-then-bleed
node window, consistent with prior forensic.

**Read these numbers ONLY as evidence the pipeline runs.** They are NOT a
conclusion, because of the limitations below.

## 6. Limitations that block any survival conclusion (foregrounded)

1. **Close-level → floor checked at realized exits only.** Intra-trade floating
   dips are invisible, so the sim reports **0 buffered breaches on a history that
   really did breach** (live 95,191 was floating + daily-flatten driven). The sim
   therefore **cannot certify survival**.
2. **Fixed exits.** `pips` embeds the actual exits incl. the 08-11 RiskGuard
   flatten; re-sizing does not recompute whether a smaller size would have avoided
   that day's daily-loss breach. `forced_flatten_equivalents` is a proxy.
3. **Reconstructed stop (20/30).** Baseline replay equity ≠ actual node equity
   (pre-08-13 used ATR stops with larger lots). Results are sensitive to this.
4. **No floating in equity-at-entry.**
5. **One adverse regime.** Same disqualifier as `RISK_UNIT_VALIDATION_DECISION`:
   train/validate are positive months, so no unit can be validated for drawdown
   survival on this data. This simulator changes the *tooling*, not that fact.

## 7. What would make the simulator conclusive (evidence to gather, not build)

Per the prior forensic's telemetry ask:
1. Per-trade **`equity_before` + `stop_pips`** logged → removes reconstruction #3/#4.
2. Per-close **`forced_by_riskguard` + triggering limit + distance-to-floor** →
   lets the sim model flatten timing (limitation #2) instead of proxying it.
3. A **second independent adverse regime** (more live history, or a tick-level
   replay generating synthetic drawdowns) → the only thing that lifts limitation
   #5 and #1 (tick replay restores intra-trade floating).

Until then this simulator is a **comparison bench and a telemetry driver**, not a
validator. It is ready to consume the above fields the moment they exist.

## 8. Run

```
python -m research.risk_engine.simulator --journal reports/signals_node.jsonl [--telemetry]
python -m research.risk_engine.simulator --journal reports/signals.jsonl --base-pct 0.011
```

Outputs `shadow_out/sim_summary.json` (+ per-policy telemetry with `--telemetry`).
