# RISK_ENGINE_SHADOW_SPEC.md

Specification for the isolated Risk Engine prototype. SHADOW / RESEARCH ONLY.
No live wiring. No recommended parameters. Located in `research/risk_engine/`.

## 1. Purpose & non-goals

**Purpose.** A pure component that, given account + trade + risk-state inputs,
returns a *hypothetical* position-sizing decision and rich telemetry — so sizing
policies can be compared offline without touching the running system.

**Non-goals (explicit).**
- Not wired into execution. Never calls MT5 / executor / RiskGuard / config.
- Does not choose a risk %. The prior forensic proved the available data cannot
  validate one (single adverse August regime; anchor-fragile cushion).
- Does not change the live node, its config, or its halt state.

## 2. Architecture

```
Signal
  → Candidate Engine (unchanged; out of scope here)
    → RiskEngine.decide(state, policy, profile)     ← PURE
        inputs: equity · stop/ATR · daily loss · floor distance ·
                open risk · correlated exposure · pip value · lot bounds
      → RiskDecision (allowed, lot, risk_pct/usd, scales, projections, reason)
        → ShadowTelemetryWriter → shadow_out/*.jsonl
          → NO EXECUTION
```

The engine is a **function of its inputs**. It holds no live handles, reads no
global state, performs no I/O (telemetry is a separate writer the caller drives).

## 3. Input — `RiskState` (models.py)

| Field | Meaning | Missing → |
|-------|---------|-----------|
| `equity`, `balance`, `initial_balance` | account | equity missing/≤0 ⇒ **reject** |
| `symbol`, `direction`, `entry_price` | candidate trade | — |
| `stop_price`, `stop_distance_pips`, `atr` | stop geometry | stop_pips missing/≤0 ⇒ **reject**; ATR optional (non-fatal) |
| `current_daily_loss_pct`, `current_drawdown_pct` | risk-state | missing ⇒ throttle degrades to scale 1.0 + warning |
| `distance_to_buffered_floor`, `distance_to_firm_floor` | cushion ($) | ≤0 buffered ⇒ **reject** (never add risk below floor) |
| `existing_positions[]`, `existing_open_risk_usd`, `correlated_open_risk_usd` | exposure | empty ⇒ no correlation throttle |
| `account_currency`, `pip_value`, `min_lot`, `max_lot`, `lot_step` | instrument | pip_value missing/≤0 ⇒ **reject** |

**Rule: never invent a missing value.** UNAVAILABLE (`None`) is distinct from
`0.0`. Load-bearing inputs (equity, stop, pip-value) cause a reject; contextual
inputs degrade the relevant throttle to report-only with a warning.

## 4. Output — `RiskDecision` (models.py)

`allowed`, `risk_pct`, `risk_usd`, `lot_size`, `base_risk`, `floor_scale`,
`correlation_scale`, `daily_loss_scale`, `final_scale`, `projected_risk_pct`,
`projected_floor_distance`, `correlated_exposure`, `decision_reason`, `warnings[]`.

`final_scale = floor_scale × correlation_scale × daily_loss_scale`;
`risk_usd = base_risk_usd × final_scale` (after lot clamps → realized risk);
`lot = risk_usd / (stop_pips × pip_value)`, clamped `[min_lot, max_lot]` then
snapped to `lot_step` (min/max before step — mirrors executor).

## 5. Policy — `RiskPolicy` (risk_engine.py)

All parameters external. **No mode is on by default.**

- `risk_mode`: `"fixed_usd"` (current node, $-budget, equity-invariant) or `"pct"`
  (equity-proportional; caller must supply `base_risk_pct` — no default good value).
- `floor_mode`: `off` | `linear_taper` | `hard_block` — throttles as the cushion
  (`distance_to_buffered_floor / equity`) shrinks. Params: taper start/end frac,
  min scale. **Keys off live distance-to-floor, not a fixed %** → cushion-robust.
- `corr_mode`: `off` | `shared_unit` (size correlated leg × `corr_shared_scale`)
  | `cap` (throttle same-USD-direction risk to `corr_cap_pct` of equity).
- `daily_mode`: `off` | `linear_taper` — throttles as `current_daily_loss_pct`
  approaches the buffered daily limit.
- `block_if_projected_breach`: hard-block if a full stop would cross the buffered
  floor. **Default False** so no threshold is imposed unless the caller opts in.

## 6. Floor model (models.PropProfile)

Calculation only — **does not change the live RiskGuard.** Mirrors
`risk_guard.py:195-223, 287-289`:

- `safety_factor = 1 − buffer/100` (node buffer 20 → 0.80)
- **Buffered floor** = `initial × (1 − dd% × safety_factor)` → node **95,200**
- **Firm floor** = `initial × (1 − dd%)` → node **94,000**
- **Buffered daily limit** = `daily% × safety_factor` → node **2.4%**
- Both static and trailing supported; node runs static.

The engine **reports distance to both floors** and never selects a threshold.

## 7. Correlation model (correlation.py) — PROTOTYPE

Conservative, explicit **USD-exposure bucket** = {EURUSD, USDJPY} (XAUUSD stubbed,
not active). We do **not** treat a correlation coefficient as sufficient — we
aggregate exposure. Mirrors `correlation_engine.py:41-55 position_usd`:

- `signed_usd_notional` (long-USD +): EURUSD BUY and USDJPY SELL are **both
  short-USD** (same sign) — the correlated bet behind the 08-11 double flatten.
- `aggregate_usd_bucket` returns net notional, gross notional, and
  **same-direction stop-risk $** (the number a cap should throttle), including
  the prospective candidate.

Do not add instruments without evidence.

## 8. Telemetry (telemetry.py)

Fields (spec): `timestamp, signal_id, symbol, direction, equity_before,
balance_before, entry_price, stop_price, stop_pips, atr, base_risk_pct,
base_risk_usd, floor_scale, correlation_scale, daily_loss_scale, final_risk_pct,
final_risk_usd, hypothetical_lot, open_risk, correlated_risk, floor_distance,
projected_floor_distance, decision, reason, warnings`.

Output **only** under `research/risk_engine/shadow_out/`. A guard raises if a path
resolves onto `reports/signals*` or outside `shadow_out/`. Never appended to any
live journal.

## 9. Safety invariants (must always hold)

1. Zero imports from `axonai`; zero MT5 calls; zero execution/close/flatten calls.
2. `decide()` is pure and deterministic; no I/O inside it.
3. Telemetry and sim outputs live only in `shadow_out/`.
4. Simulator opens journals read-only; never writes `reports/`.
5. `git status` shows only new **untracked** files under `research/risk_engine/`;
   no production file modified.

## 10. Test matrix (tests/test_risk_engine.py — 28 tests, all offline)

normal sizing (pct + fixed + determinism) · declining equity (pct shrinks,
fixed invariant) · approaching floor (taper reduces; off by default) · daily-loss
consumption (taper; off by default) · two correlated positions (shared_unit
halves; cap throttles to budget) · independent positions (opposite USD sign not
scaled) · simultaneous correlated signals (reads passed open-set, not globals) ·
insufficient floor distance (already-breached reject; projected-breach block only
when opted in; reported otherwise) · min lot (bump raises risk + warns) · max lot
(cap reduces risk + warns) · zero/negative/missing stop (reject) · missing ATR
(non-fatal) · invalid equity 0/neg/None (reject) · missing pip-value (reject) ·
invalid policy (reject) · prop-profile floors (95,200 / 94,000 / 2.4%) ·
USD-netting directions.
