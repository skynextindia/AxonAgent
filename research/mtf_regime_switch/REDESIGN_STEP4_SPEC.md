# Redesign Step 4 — Target Engine Spec: Wide-TP Regime-Selected Fader (WRSF)

**Status:** DESIGN ONLY (2026-09-03). Not built, not armed. Steps 1–2 (regime labels +
selector shadow) are built and read-only; this spec is the end-state Step 4 aims at, to be
built OFF/shadow-first and armed only after the Sept-10/21 validation + user approval.

---

## 1. Why (the evidence this replaces)

The current live engine — exhaustion-peak fade, ±20p hard bracket, ATR trail, blind to
regime — is **negative-expectancy on its own recorded tick data**:

- EURUSD winners capture only **~45% of MFE** (trail give-back); wins ~+4.4p, losers pay the
  full −20p → **−3.50p/trade**, needs ~82% win rate just to break even.
- **No exit tuning fixes it** — a fixed +3p TP only halves the bleed (−1.79p), still negative.
  The leak is *losing entries hitting the stop*, which an exit cannot repair.
- Direction is a **random walk** at every intraday horizon (VR≈1.0). The one exploitable
  structure is **multi-day mean-reversion** (VR≈0.90 at 120h), harvested only by a **wide TP
  + cheap entry**, and the edge is **cost-bound**.

The one net-positive path found in 2y/12-quarter research (see memory `expectancy-verdict`,
`randomness-edge`, `mtf-regime-goodspots`):

> **Wide bracket (SL20 / TP100, 1:5) + limit/cheap entry + regime selection** = net +0.98p
> baseline, lifted to **+1.11p / 6-of-10 quarters** by taking only good-spot regimes.

WRSF operationalizes exactly that.

---

## 2. Target architecture (one paragraph)

Keep the existing **exhaustion-peak-at-S/R detector** as the *where/when* trigger (it is a
location+timing engine, not a direction edge). Replace everything downstream: the fade
direction is **filtered by the good-spot selector** (`good_spot_decision`, Step 2) — take
aligned/range fades, skip counter-trend (or flip to with-trend), using the **net/range HTF
regime** (Step 1). Entry is a **limit/cheap fill at the level** (the edge is cost-bound), the
bracket is a **fixed SL20 / TP100 (1:5)** with **no tight trail**, held to TP, SL, or a
~5-day max-hold. Sizing risks a constant slice off the 20p SL; the per-trade edge is small by
construction (≈17% win at +96p) so the lever is **trade count × sizing**, not per-trade pips.

---

## 3. Component spec

### 3.1 Trigger (unchanged)
- Reuse `PeakDetector` microstructure exhaustion + the LIVE PEAK GATE (S/R proximity) and
  the range-extreme gate. These give a peak, a direction (fade), and a level. **No change.**

### 3.2 Direction selection (NEW — the core)
- Call `good_spot_decision(fade_dir, mtf_stamp, htf_key, flip_counter_trend)` (Step 2).
  - `take`  → enter the fade (as detected).
  - `skip`  → no entry.
  - `flip`  → enter the **with-trend** direction (only if `flip_counter_trend` — the
    "go with the market" mode; default OFF pending validation of the flip side).
- Regime from the **net/range** trend label (Step 1), `htf_key` default `1D`.
- **Candidate refinement (validate first):** add an **intraday-location** gate — take a SELL
  only at deep-premium (≥~85%), a BUY only at deep-discount (≤~15%). Live 2026-09-03 evidence:
  deep-premium fades won, mid-premium (61%) fades ran to the full stop. Ship only if the
  shadow confirms location separates winners.

### 3.3 Execution (NEW — cost is make-or-break)
- **Limit entry at/near the S/R level**, not a market fill. The edge is +0.98p at ~1.2p cost
  and evaporates at ~2p; a market fill that pays spread + slippage can erase it. This is the
  single highest-risk assumption — see §7.
- If the limit is not filled within a small window / N bars, **cancel** (no chase). A missed
  entry is free; a bad-cost entry is not.
- Fill-quality telemetry (requested vs filled price) logged on every entry to measure the
  realized cost against the +0.98p budget.

### 3.4 Bracket & exit (NEW)
- **SL = 20p, TP = 100p (1:5)**, fixed. `wtms_sl_pips` / `wtms_tp_pips` already carry these.
- **No breakeven, no tight trail** (the trail is what clipped the scalp winners). Optionally a
  *wide/late* trail as a shadow variant, but the validated base is fixed brackets.
- **Max hold ~120h** (5 trading days) → force-close. Reuse `wtms_max_hold_hours`.
- Exit is therefore one of {TP100, SL20, max-hold}. Deterministic, cheap to reason about.

### 3.5 Sizing & risk
- Risk a constant % (≈1.1%) off the **20p SL**, unchanged from today (sizing is off the SL,
  so 1:5 does not change per-trade risk).
- **Fewer, more selective trades** (skip cuts trade count). Net edge = count × per-trade × size.
- Interacts with the **$300 daily-loss cap** and the account RiskGuard — a run of −20p losses
  before a +100p winner is expected (17% win rate); the cap must tolerate the loss-cluster
  variance. **Model the drawdown distribution before arming** (open question §7).

### 3.6 Inverse mirror (decision required)
- The USDJPY inverse mirror **doubles USD exposure** (not a hedge). Under WRSF, EURUSD entries
  become selective and wide-held. Options: (a) mirror the WRSF entry with a wide USDJPY
  bracket too; (b) keep the coupled-exit mirror as-is; (c) **drop the mirror** for the redesign
  and run EURUSD-only. **Recommend (c) for the first armed version** — one clean instrument,
  no doubled exposure, simpler validation. Revisit the mirror as a separate experiment.

---

## 4. Config surface (proposed, all default OFF)

```
redesign_engine_enabled: False        # master; OFF = current scalp engine unchanged
redesign_shadow_only:    True         # log would-enter/would-exit, place NO order
redesign_use_selector:   True         # gate entries by good_spot_decision
redesign_flip_counter_trend: False    # skip (False) vs flip-to-with-trend (True)
redesign_entry: "limit"               # "limit" (validated) | "market" (disprove only)
redesign_entry_timeout_bars: 2        # cancel an unfilled limit after N bars
redesign_sl_pips: 20 / redesign_tp_pips: 100 / redesign_max_hold_hours: 120
redesign_intraday_gate: False         # candidate location refinement (§3.2)
redesign_mirror: "off"                # off | wide | coupled
```
Reuses `mtf_trend_measure` (net/range), `goodspot_htf_key`. Fully reversible: master flag OFF.

---

## 5. Built vs. remaining

| Piece | Status |
|---|---|
| Net/range regime labels (Step 1) | ✅ built (`9dc6e09`) |
| `good_spot_decision` selector + shadow (Step 2) | ✅ built (`c3f4688`) |
| Wide-TP outcome logging (wtms shadow) | ✅ built (`1295f88`) |
| **Limit-entry execution path** | ❌ Step 4 |
| **WRSF entry/exit engine (shadow-first)** | ❌ Step 4 |
| **Fill-quality / realized-cost telemetry** | ❌ Step 4 |
| **Drawdown-distribution model (17% win, 1:5)** | ❌ Step 4 (pre-arm) |
| **Mirror decision** | ❌ Step 4 (recommend EURUSD-only) |

---

## 6. Arming path (gates, in order)

1. **Flat restart** to activate Steps 1–2 → collect net/range-labelled wtms rows + selector
   verdicts.
2. **Sept-10 health read** — is the map accumulating regime diversity + TP-reachers?
3. **Sept-21 go/no-go** — SELECTOR net (take+flip) beats blind fade, **n ≥ 60 across ≥ 2
   regimes**, net > 0 **at limit cost**, favors against-trend/reversion where the data says so.
4. If it holds → build WRSF **shadow-first** (`redesign_shadow_only: True`): logs would-enter/
   exit + realized-cost, still places no order. Validate the *executable* path forward.
5. **User approval** → arm live (`redesign_shadow_only: False`), EURUSD-only, small size, with
   the daily cap + RiskGuard as backstops. Reversible via the master flag.

No step is autonomous; §3.3 limit-fill reality and §5 drawdown model must clear before live.

---

## 7. Risks & open questions (ranked)

1. **Cost realism (make-or-break).** The edge is limit-cost-bound. Can we actually get filled
   at/near the level often enough, or does selectivity push us to market fills that pay the
   spread and kill the +0.98p? The shadow measures *outcomes*, not *fills* — Step 4's
   fill-quality telemetry is the real test. If limit fills are rare, WRSF may be unshippable.
2. **Drawdown of a 17%-win / 1:5 profile.** Long loss clusters before a winner. Model the
   worst-case string vs the $300 cap and account RiskGuard before arming.
3. **Regime-label stability forward.** Net/range is better than ER but still a lagging label;
   the good-spot up/down asymmetry (buy-up-dips lost in-sample) may be sample-specific.
4. **5-day holds:** overnight swap, weekend gap, and the one-position-per-pair guard throttling
   trade count. Reconcile with the weekend-only EOD flatten.
5. **Small live sample:** everything rides on the shadow validating forward; a range-only or
   censored slice (like the current 50 rows) is *not* sufficient — the Sept-21 gate enforces
   ≥2 regimes and TP-reachers precisely to avoid arming on a censored slice.

---

## 8. Non-goals

- Not a new *direction predictor* — direction stays a fade/with-trend of a detected peak;
  WRSF only **selects** and **holds wider**. Direction remains a coin flip; the edge is
  regime selection + mean-reversion capture + cost control, not forecasting.
- Not an exit-tuning of the current scalp (that path is exhausted — see §1).
- Not a change to the detector/gates (the *where/when* is kept).

---

*Related: memory `mtf-regime-goodspots`, `expectancy-verdict`, `randomness-edge`,
`eurusd-mfe-giveback`, `live-config-state-2026-08-21`. Code: `good_spot.py`,
`wtms_shadow_report.py`, `mtf_cross_regime_spots.py`, daemon `_arm_wtms_setup` /
`_compute_mtf_stamp`.*
