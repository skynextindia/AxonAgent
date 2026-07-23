# AxonAI Dashboard Gap Analysis
**Focus: Unified Entry System & Gold Trailing System Integration**

## Overview
With the successful implementation of the **Unified Entry System** (M15 Candle Setup Tracker + Unified Confluence Scoring) and the **Gold Trailing Fix** (ATR Floors + EMA Smoothing), the backend logic has advanced significantly. 

However, the Web Dashboard (`cli/static/index.html`) and the WebSocket broadcaster (`axonai/realtime/api_server.py` and `daemon.py`) have not been updated to reflect these new structural mechanisms. This creates an observability gap where the system makes decisions based on data the user cannot see.

---

## 1. Missing Backend Broadcasts (Data not being sent)

The `trigger_metrics` event broadcasted in `daemon.py` (Lines ~1243-1263) currently lacks the new system states:

1. **Candle Setup State**: 
   - `setup_active` (boolean): Gating the `IDLE` state.
   - `setup_direction` (BUY/SELL): The direction of the setup.
   - `setup_score` (0.4 - 1.0): The strength of the M15 setup.
   - *Why it's needed*: Without this, the user cannot see when the system is "armed" and waiting for a velocity tick vs when it is completely asleep due to no M15 setup.
2. **Unified Confluence Score Breakdown**:
   - The backend aggregates a score based on 4 weights (Candle 30%, Velocity 25%, Proximity 25%, H4/H1 20%). Currently, it only outputs the final `signal_quality` and a text `reason`. 
   - *Why it's needed*: The dashboard should display a radar chart or progress bars showing *which* confluence parameters are met.
3. **ATR Trailing Floor State**:
   - The `trail_state` broadcast does not indicate if the trailing stop is currently clamped by the `1.0 * H1_ATR` floor (which is crucial for XAUUSD).

---

## 2. Missing Frontend UI Components (Dashboard UI)

The frontend `index.html` needs specific UI elements to visualize the new backend states:

1. **M15 Setup Readiness Indicator (Cockpit Tab)**:
   - Needs a prominent "Setup Status" badge (e.g., `M15 SWEEP DETECTED - ARMING BUY` or `NO ACTIVE SETUP`).
   - Should be integrated into the `SECURITY_GATES_MATRIX` or `TACTICAL_OVERRIDES` area.
2. **Unified Confluence Matrix Widget (Intelligence Tab)**:
   - A visual breakdown of the 4-part score. Instead of just showing `CONVICTION: 75%`, it should show:
     - `[====      ] 15/30 M15 Setup`
     - `[========  ] 20/25 Velocity Exhaust`
     - `[==========] 25/25 Level Proximity`
     - `[======    ] 15/20 H4 Trend`
3. **Trailing Stop Floor Indicator (Trade State Tab)**:
   - During an active trade, if the trailing distance hits the ATR floor, a badge should appear: `ATR FLOOR ACTIVE (8.5 pips)`.

---

## 3. What is NOT Necessary (Avoid Clutter)

To keep the dashboard lightweight and performant, we should **avoid** building the following:

1. **Tick-by-tick EMA smoothing values**: The Gold trailing fix added an EMA with `alpha=0.02`. Broadcasting the raw EMA value 10 times a second is unnecessary network overhead. The backend handles the smoothing silently.
2. **Intra-candle sweep tracking**: The dashboard chart (`lightweight-charts`) should not attempt to visually draw the partial sweeps of a live M15 candle. The `CandleSetupTracker` handles this internally and emits a simple `setup_active` state. Let the chart remain standard OHLC.
3. **Legacy Hard-Reject reasons**: The old `_reversal_confluence_grade` vetoes are now unified. We don't need a UI panel listing all 10 legacy fail-safe rules, just the unified score breakdown.

---

## Conclusion & Next Steps
The backend is fundamentally executing perfectly according to the new logic, but it operates in the dark from the user's perspective. 

**Recommended Action**: 
1. Update `daemon.py` to inject `candle_setup` metrics into the `"type": "trigger_metrics"` WebSocket payload.
2. Patch `cli/static/index.html` to display the "M15 Setup Status" in the top status bar or Security Gates matrix.
