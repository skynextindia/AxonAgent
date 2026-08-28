# Risk Engine — isolated prototype (SHADOW / RESEARCH ONLY)

A **pure-calculation** risk/sizing component and a **read-only** historical
simulator. Nothing here is wired into the live trading path. It exists to answer
sizing questions *offline* without touching the running system.

> **This is a bench, not a strategy.** No risk percentage, floor threshold, or
> correlation cap in this package is a recommended production parameter. The
> illustrative policy values (incl. `0.011`) are the *current* live values or
> arbitrary bench settings, provided so the models are comparable. See
> `../../RISK_ENGINE_SIMULATION_PLAN.md` equivalents in this folder.

## Hard isolation guarantees (verified 2026-08-18)

| Guarantee | How |
|-----------|-----|
| No production import | grep-verified: no production module does `import research` / `from research`. `research/` has no `__init__.py` at its root and is not on any live import path. |
| No `axonai` dependency | Every production formula (sizing, floor, pip-value, USD netting) is **re-implemented locally**, with the source `file:line` it mirrors cited in a comment. The package imports nothing from `axonai`, so the running daemon/executor/RiskGuard cannot be imported or side-effected. |
| No MT5 / execution | No MT5 API, no `execution_bridge`, no `trade_executor` send/close, no RiskGuard flatten, no config mutation, no restart. |
| No live journal writes | Telemetry writes **only** under `research/risk_engine/shadow_out/`. A runtime guard (`telemetry._assert_isolated`) raises if a path resolves onto `reports/signals*` or outside `shadow_out/`. |

## Layout

```
research/risk_engine/
  __init__.py           package doc + isolation notes
  models.py             RiskState (input), RiskDecision (output), PropProfile
  correlation.py        EURUSD+USDJPY USD-exposure bucket (prototype)
  risk_engine.py        decide(state, policy, profile) -> RiskDecision  (PURE)
  telemetry.py          ShadowTelemetryWriter -> shadow_out/*.jsonl
  simulator.py          read-only journal replay under A–F policies
  tests/                28 unit tests, no MT5/network
  shadow_out/           generated telemetry + sim summaries (disposable)
  README.md
  RISK_ENGINE_SHADOW_SPEC.md
  RISK_ENGINE_SIMULATION_PLAN.md
```

## The engine

```python
from research.risk_engine.models import RiskState, NODE_PROFILE
from research.risk_engine.risk_engine import RiskPolicy, decide

state  = RiskState(equity=100_000, symbol="EURUSD", direction="BUY",
                   entry_price=1.15, stop_distance_pips=20, pip_value=10.0,
                   distance_to_buffered_floor=4_800)
policy = RiskPolicy(name="pct", risk_mode="pct", base_risk_pct=0.011)
d = decide(state, policy, NODE_PROFILE)
# d.allowed, d.lot_size, d.risk_pct, d.floor_scale, d.correlation_scale,
# d.daily_loss_scale, d.final_scale, d.projected_floor_distance, d.decision_reason
```

**Signal → Candidate Engine → `decide()` → hypothetical size → telemetry → NO EXECUTION.**

- **Inputs** the caller cannot obtain safely are passed as `None` (UNAVAILABLE);
  the engine reports them as warnings and refuses to size on the load-bearing
  ones (equity, stop, pip-value) rather than inventing a value.
- **Scales default OFF** (`floor_mode`/`corr_mode`/`daily_mode = "off"`, scale
  1.0). No throttle threshold is imposed unless the caller passes its parameters.
- **Deterministic**: identical inputs → identical output.

## The simulator (read-only)

```
python -m research.risk_engine.simulator --journal reports/signals_node.jsonl [--telemetry]
```

Replays the FULL journal, re-sizes each trade under policies A–F, holds the
realized price path (`pips`) fixed, and reports metrics + month/symbol/direction/
regime breakdowns to `shadow_out/sim_summary.json`. It **isolates the sizing
effect**; it does not re-simulate RiskGuard flatten *timing*.

### Documented limitations (do not read past these)

1. **Close-level data** — equity/floor are evaluated at each realized EXIT, so
   intra-trade floating dips are invisible. True drawdown is deeper than reported,
   and the sim can show 0 buffered-floor breaches on a history that really did
   breach (the live 95,191 breach was floating + daily-flatten driven). This is
   why the sim yields **no survival verdict**.
2. **Fixed exits** — `pips` already embeds the actual exits (incl. the 08-11
   RiskGuard flatten). Re-sizing scales P&L on those same exits; it does not
   recompute whether a smaller size would have avoided that day's flatten.
   `forced_flatten_equivalents` is a close-granularity proxy.
3. **Reconstructed stop** — no per-trade stop field in the journal; stops are
   reconstructed at hard-distance 20/30 pips. Baseline replay equity therefore
   differs from the actual node (pre-08-13 used ATR stops with larger lots).
4. **Equity-at-entry excludes floating** (not in the journal).

These are the same constraints that made the risk-unit question *unvalidatable*
in the prior forensic (`RISK_UNIT_VALIDATION_DECISION`): one adverse regime,
anchor-fragile cushion, no per-trade stop/equity telemetry.

## Tests

```
python -m unittest research.risk_engine.tests.test_risk_engine
```

28 tests, all offline: normal sizing, declining equity, approaching floor,
daily-loss consumption, correlated vs independent positions, simultaneous
correlated signals, insufficient floor distance, min/max lot, zero/invalid stop,
missing ATR (non-fatal), invalid equity, missing pip-value, invalid policy,
prop-profile floors, USD-netting directions.

## What this package deliberately does NOT do

- It does **not** pick or recommend a risk %. (Prior work proved the data cannot
  validate one — single adverse regime.)
- It does **not** connect to the live path, and it must not be wired in without a
  separate, explicit decision.
- It does **not** optimize parameters. The simulator runs a fixed configured set.
