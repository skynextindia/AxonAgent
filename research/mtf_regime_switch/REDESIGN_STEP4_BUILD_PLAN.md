# Step 4 Build Plan — Wide-TP Regime-Selected Fader (WRSF)

**Status:** PLAN FOR REVIEW (2026-09-11). Not built, not armed. Builds on `REDESIGN_STEP4_SPEC.md`.
Under the freeze: everything below is local, shadow-first, reversible, and arming needs explicit
user approval — no autonomous live-money change.

---

## 0. What changed since the spec (this session's findings)

Three results materially simplify the build:

1. **Cost is ~0.70p (EURUSD), not 1.2–2.0p** — and it's almost all commission, which a limit order
   *cannot* cut (`limit-entry-cost-model`). **Consequence: the limit-entry engine is no longer needed.**
   The spec's #1 make-or-break risk ("can we get limit fills cheap enough?") is **retired** — market
   entry at 0.70p already clears the edge.
2. **At real cost the edge is fatter:** wide-TP + 2D selector = **+1.51p/trade, 6/10q, n=17,332**
   (blind wide-TP already +0.69p). Strengthens the case to build.
3. **Intraday location is sub-cost as a standalone gate** (`location-revert-vs-break`: ~52–54%,
   only break-even). **Consequence: drop the §3.2 intraday-location gate** from the build — it doesn't
   pay on its own; the selector's RANGE/DOWN handling already captures what's real.

Net effect: WRSF gets **simpler** — reuse the existing market-entry executor, no limit-fill path, no
location gate. The core new work is the selector-gated entry + wide fixed bracket + long hold.

---

## 1. Scope of the build

**Keep unchanged:** the exhaustion-peak-at-S/R detector, the LIVE PEAK GATE, the range-extreme gate,
and the guards armed this session (re-entry guard, follower range gate). These are the *where/when* and
the bounded safety skips — WRSF inherits them.

**Replace downstream:** direction is filtered by `good_spot_decision` (Step 2, 2D htf, skip_up_buy ON);
the bracket becomes fixed **SL20/TP100** with **no breakeven/trail**, held to TP/SL/~120h; **market
entry** (existing executor); **EURUSD-only** (mirror off).

---

## 2. Phases

### Phase A — WRSF shadow engine (no orders)
Build the full decision path as a shadow that logs would-enter/would-exit but places nothing.
- New method `_wrsf_decide(signal, event)`: run `good_spot_decision`; on `take` emit a virtual WRSF
  entry (market price, SL20/TP100); `skip` → log skip. (`flip` stays OFF.)
- Resolve virtual brackets on M15 close (reuse the wtms resolver logic) → log to
  `reports/wrsf_shadow.jsonl`: entry, regime, verdict, TP/SL/maxhold outcome, gross & net-of-0.70p.
- Reader `wrsf_shadow_report.py`: net/quarter, by regime, vs blind fade, TP-reacher count.
- Config: `wrsf_shadow_enabled` (default True, read-only). **Deliverable: live would-trade log.**

### Phase B — Drawdown / risk model (pre-arm gate, analysis only)
- From the 2.4y backtest + shadow rows, simulate the **equity path** of a 17%-win / 1:5 profile at
  ~1.1% risk: worst loss-cluster length, max drawdown, interaction with the **$300 daily cap** and
  RiskGuard. Deliverable: "can the account sit through the expected −20p strings?" go/no-go number.
- Decision output: sizing % and whether the daily cap needs adjustment for the loss-cluster variance.

### Phase C — Executable engine behind a master flag (still shadow by default)
- `_wrsf_decide` gains a live branch: on `take`, place a real market order SL20/TP100 via the existing
  executor, register with live_state, hold to TP/SL/maxhold (no trail). Reuse `_last_dir_exit`,
  re-entry guard, daily cap, EOD flatten.
- **Gated by TWO flags:** `redesign_engine_enabled` (master, default False) AND `redesign_shadow_only`
  (default True). Live only when master ON and shadow_only OFF. EURUSD-only; `redesign_mirror: off`.
- Fill-quality telemetry (requested vs filled) logged to confirm live cost ~0.70p as modeled.

### Phase D — Forward validation → arm (user approval)
- Run Phase A shadow forward until: **n ≥ 60 across ≥ 2 regimes**, net > 0 at real 0.70p cost, TP-reachers
  present (not a censored range slice), selector beats blind fade. (The existing `selector_forward`
  cutoff + wtms rows feed this.)
- If it holds → present results + Phase B drawdown model → **user approves** → flip `redesign_shadow_only`
  OFF at a flat restart, EURUSD-only, reduced size. Reversible via master flag.

---

## 3. Config surface (all default OFF/shadow)

```
redesign_engine_enabled:  False     # master; OFF = current scalp engine unchanged
redesign_shadow_only:     True      # True = log only, place NO order
wrsf_shadow_enabled:      True      # Phase A read-only logger
redesign_use_selector:    True      # gate entries by good_spot_decision (2D, skip_up_buy ON)
redesign_sl_pips: 20  redesign_tp_pips: 100  redesign_max_hold_hours: 120
redesign_entry: "market"            # market entry (cost 0.70p already clears the edge)
redesign_mirror: "off"              # EURUSD-only for v1
```
Dropped from the spec: `redesign_entry:"limit"`, `redesign_entry_timeout_bars`, `redesign_intraday_gate`
(all obsoleted by this session's cost + location findings).

---

## 4. Files to touch

| File | Change |
|---|---|
| `axonai/realtime/daemon.py` | `_wrsf_decide` (Phase A shadow → Phase C live branch); wrsf resolver on M15 close |
| `axonai/default_config.py` | WRSF config block (above), per-pair (EURUSD-only) |
| `research/mtf_regime_switch/wrsf_shadow_report.py` | NEW reader (net/quarter, by regime, vs blind) |
| `research/mtf_regime_switch/wrsf_drawdown_model.py` | NEW (Phase B equity-path / DD sim) |
| `REDESIGN_STEP4_SPEC.md` | annotate §3.3/§3.2/§7.1 as superseded by cost+location findings |

---

## 5. Decisions needed from you (before Phase A)

1. **Market entry (recommended) vs still building limit** — this session says market at 0.70p is fine
   and removes the riskiest component. Confirm we drop the limit engine.
2. **EURUSD-only for v1 (recommended)** vs keeping the USDJPY inverse mirror. Mirror doubles USD risk
   and the corr-gate/doubling losses argue for off; revisit later as its own experiment.
3. **Coexistence:** WRSF runs *alongside* the current scalp during shadow (both log). Do we eventually
   *replace* the scalp, or run WRSF as a second selective sleeve? (v1 recommendation: replace, once armed.)
4. **Drawdown tolerance:** a 17%-win / 1:5 engine will show long red strings. Confirm you're prepared to
   sit through them, and whether the $300 daily cap stays or flexes for the loss-cluster variance.

---

## 6. What stays true regardless (guardrails)

- Nothing autonomous. Shadow-first; live only after forward validation + your approval at a flat restart.
- Reversible via `redesign_engine_enabled`. The current scalp engine is untouched while the master flag is OFF.
- The edge is **regime selection + wide-TP mean-reversion capture + low cost**, NOT direction forecasting
  (direction stays a coin flip). Feast/famine (6/10q) is expected, not a bug.

*Related: `REDESIGN_STEP4_SPEC.md`, memory `mtf-regime-goodspots`, `expectancy-verdict`,
`limit-entry-cost-model`, `location-revert-vs-break`, `randomness-edge`.*
