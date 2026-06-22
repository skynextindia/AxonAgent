# Market Reversal Detection Failure Analysis

> **Date**: 2026-05-30  
> **Scope**: Tick data velocity/delta measurement across PeakDetector, TickEngine, EventDetector, LiveState  
> **Status**: DRAFT — awaiting approval before any code changes

---

## Executive Summary

The system has **two independent, unconnected velocity measurement systems**. The root `tick_engine.py` (TickProcessor) computes sophisticated tick-level signals — velocity collapse, aggression shifts, absorption — but **none of this data reaches** the realtime `PeakDetector` or `EventDetector`. The realtime layer computes its own velocity from scratch using only a 50-tick rolling buffer, losing the richer multi-window context from the lower layer.

Furthermore, the `PeakDetector` has **no concept of peak sequence** — it never classifies peaks as Higher Highs (HH), Lower Highs (LH), Higher Lows (HL), or Lower Lows (LL). Without a state machine tracking peak structure, genuine trend reversals (where a HH→LH transition or LL→HL transition occurs) are invisible at the tick level. The system can feel velocity change on a per-tick basis but cannot answer *"is this the end of the trend?"*.

---

## Root Cause Analysis

### Root Cause #1: Dual Decoupled Velocity Systems

There are two completely separate velocity computation engines with **no shared data**:

| Aspect | Root `tick_engine.py` (TickProcessor) | `realtime/peak_detector.py` (PeakDetector) |
|---|---|---|
| **Velocity formula** | `Σ\|Δprice\| / time_span` over 10s window | `\|Δprice\| / Δt / pip_mult` per single tick |
| **History depth** | 15,000 ticks (~25 min at 100ms) | 50 ticks (~5 seconds at 100ms) |
| **Directional tracking** | buy_vol / sell_vol via imbalance | buy_velocities / sell_velocities deques |
| **Collapse detection** | ✅ `velocity < 0.3 × avg_velocity` | ✅ Rule A: `current_vel < 0.3 × max_vel` |
| **Imbalance** | ✅ 10s/60s/300s windows with confluence | ❌ Not computed |
| **Aggression shift** | ✅ `imbalance flip > 0.5` reversal signal | ❌ Not computed |
| **Absorption** | ✅ High vol but price moves < 1 pip | ❌ Not computed |
| **Output destination** | `DecisionEngine` (dead-end, trivial logic) | Only place used |

**The TickProcessor's rich signals are computed, stored in `SignalState`, and then fed to a `DecisionEngine` with trivial heuristic logic that is never invoked in the realtime daemon path.** The realtime path uses `EventDetector` → `PeakDetector` which starts from zero.

### Root Cause #2: 50-Tick Lookback Is Too Short

```
PeakDetector buffers: maxlen=50
Tick interval:        100ms (default)
Effective history:   ~5 seconds
```

At 100ms polling, 50 ticks is only 5 seconds of market data. This means:

- **Velocity divergence** compares acceleration over ~2–3 seconds → captures noise, not structure
- **Price-per-tick efficiency** measures only the last 5 seconds → meaningless for trend context
- **Rule C (fractal swing)** uses 11 ticks (~1.1 seconds) → pure microstructure noise
- **No way to distinguish** a micro-pullback from a genuine trend reversal

### Root Cause #3: No Peak Sequence Classification (HH/HL/LH/LL)

The `PeakDetector` detects individual peaks but **never labels them in trend context**:

```
What the system sees:          What's actually happening:
  Peak @ 1.16510                LH (lower high)  ← TREND REVERSAL SIGNAL
  Peak @ 1.16530                HH (higher high)  ← trend continuation
  Peak @ 1.16480                HL (higher low)   ← trend continuation
  Peak @ 1.16500                LL (lower low)    ← TREND REVERSAL SIGNAL
```

Without a state machine tracking the sequence of peaks, the system cannot detect:
- **HH → LH transition**: Potential bearish reversal (downtrend beginning)
- **LL → HL transition**: Potential bullish reversal (uptrend beginning)
- **Failure swings**: Price fails to make a new high/low beyond the previous peak

### Root Cause #4: Cooldowns Create Blind Spots

```
Rule A (Velocity climax):  30s cooldown
Rule B (Microstructure):   15s cooldown
Rule C (Local swing):     120s cooldown
Audit Fix 2:              30s + 2 pips
```

After any peak fires, subsequent activity is suppressed. In fast-moving forex markets, a full reversal sequence can complete in 10–20 seconds. The system sees the initial climax, goes silent, and misses the actual reversal.

**Compounding issue**: The `Audit Fix 2` adds a second cooldown layer: 30s AND 2 pip movement required before re-firing. This means a failed reversal attempt that needs to re-test also gets suppressed.

### Root Cause #5: Velocity Divergence Formula Is Unstable

```python
# peak_detector.py — simplified
buy_acc = (buy_velocities[-1] - buy_velocities[-2]) / dt    # acceleration
sell_acc = (sell_velocities[-1] - sell_velocities[-2]) / dt

if dominant_side == "buy":
    velocity_divergence = sell_acc - buy_acc   # positive = sell accelerating
elif dominant_side == "sell":
    velocity_divergence = buy_acc - sell_acc   # positive = buy accelerating
```

The formula uses **acceleration difference** (second derivative of price) computed from a *single tick step*. Acceleration from one tick to the next is dominated by noise:

- A single large spread tick can flip the divergence value
- The 0.4 / 0.6 / 1.0 thresholds are arbitrary with no calibration against actual reversal data
- No smoothing or rolling average of the divergence value

### Root Cause #6: Tick-Engine Signals Never Reach Event Detector

The root `tick_engine.py` computes valuable signals that directly address reversal detection:

| Signal | What it measures | Currently used? |
|---|---|---|
| `velocity_collapse` | Current vel < 30% of average | Dead-end in DecisionEngine |
| `aggression_shift` | Imbalance flipped > 0.5 in 30s | Dead-end in DecisionEngine |
| `absorption` | High volume but price stuck | Dead-end in DecisionEngine |
| `imbalance_10s/60s/300s` | Buy vs sell volume pressure | Dead-end in DecisionEngine |
| `spread_delta` | Spread expansion signal | Dead-end in DecisionEngine |

These signals could be fed into the `EventDetector` to produce peak/reversal events, but they exist in a completely separate code path.

### Root Cause #7: Momentum Divergence Uses Only 4 Data Points

```python
# event_detector.py momentum divergence
# _rsi_history has maxlen=10, requires >= 4
if prices[-1] > max(prices[-4:-1]) and rsis[-1] < max(rsis[-4:-1]):
    # bearish divergence: price HH, RSI LH
```

With only 4 M15 candles (~1 hour of data), this captures micro-divergences that are statistically insignificant. Classic RSI divergence requires a more meaningful lookback (typically 14+ periods) to filter noise.

---

## Data Flow Diagram

```
MT5 Ticks (100ms)
    │
    ├──► TickEngine (root/tick_engine.py)
    │       │
    │       ├──► TickProcessor.process()
    │       │       ├── velocity (10s window, pips/sec)        ──┐
    │       │       ├── imbalance_10s/60s/300s                  │  DEAD END
    │       │       ├── velocity_collapse (< 0.3× avg)          │  → DecisionEngine
    │       │       ├── aggression_shift (imbalance flip)       │  (trivial, unused)
    │       │       ├── absorption (vol but no price move)      ──┘
    │       │       └── SignalState dataclass
    │       │
    │       └── DecisionEngine (trivial heuristic)  ← NOT WIRED TO REALTIME
    │
    └──► EventDetector (realtime/event_detector.py)
            │
            ├──► PeakDetector.update()  ← SEPARATE VELOCITY SYSTEM
            │       ├── tick_velocities (pips/sec per tick, maxlen=50)
            │       ├── buy_vel/sell_vel (directional, maxlen=50)
            │       ├── velocity_divergence (accel diff, 1-tick step)
            │       ├── price_per_tick_efficiency (5s window)
            │       ├── Rule A: velocity climax (3× avg, >15 pips/s)
            │       ├── Rule B: microstructure (div>1.0 & eff<0.08)
            │       └── Rule C: fractal swing (11 ticks)
            │
            ├──► _check_momentum_divergence()  ← ONLY 4 DATA POINTS
            ├──► _check_structure_break()      ← CANDLE LEVEL (not tick)
            ├──► _check_sweep()                ← CANDLE LEVEL (not tick)
            └──► _check_candle_pattern_at_level() ← CANDLE LEVEL (not tick)

RESULT: Tick-level velocity signals and candle-level structure signals
        exist in parallel silos with NO CROSS-FEED
```

---

## Why Decisions Change Per Tick

When the user observes the system *"changing decisions per tick moment"*, this is the mechanistic explanation:

1. **PeakDetector uses 5 seconds of data** → any new tick shifts 20% of the buffer
2. **Velocity divergence uses raw acceleration** → a single spread-widening tick can spike divergence from 0.3 → 1.2, triggering Rule B, then the next tick normalizes it back down
3. **No HH/HL state machine** → the system has no memory of *"the last peak was higher than the one before"* — it only sees the current 5-second window
4. **PeakDetector processes every tick** → `on_tick()` calls `_check_peak_detection()` every 100ms, so every tick generates a new potential signal

The result: velocity/divergence values oscillate wildly tick-to-tick, and the system has no higher-level structure to stabilize its interpretation.

---

## What a Proper Reversal Detection Needs

A genuine trend reversal at the microstructure level progresses through identifiable stages:

```
Stage 1: Trend Maturity
  - Price making HH/HL sequence (uptrend) or LL/LH sequence (downtrend)
  - Velocity sustained above average
  - Imbalance consistently in one direction

Stage 2: Exhaustion
  - Velocity begins declining from peak (but price still advancing)
  - Imbalance starts converging toward zero
  - Absorption: volume increasing but price range narrowing

Stage 3: Transition
  - First failure: price fails to make a new HH (or LL)
  - Peak sequence changes: last HH was lower than previous (LH formed)
  - Velocity divergence: opposing ticks gaining strength
  - Aggression shift: imbalance flips sign

Stage 4: Reversal Confirmation
  - Structure break: price breaks the last HL (uptrend) or LH (downtrend)
  - Sweep of the last swing level
  - New peak sequence: LL/HL (downtrend) or HH/LH (uptrend) begins
```

**The current system attempts to jump directly to Stage 2/3 without Stage 1 context, and has no mechanism for Stage 4 confirmation.**

---

## Components That Would Need Changes

| File | What would change |
|---|---|
| `axonai/realtime/peak_detector.py` | Buffer sizes, peak sequence state machine, velocity smoothing |
| `axonai/realtime/event_detector.py` | Feed tick-engine signals into event pipeline |
| `axonai/realtime/live_state.py` | Additional state for peak sequence tracking |
| `axonai/realtime/tick_engine.py` | Expose TickProcessor signals via callback |
| `root tick_engine.py` | Bridge the two velocity systems (or deprecate) |

---

## Proposed Approach (Not Yet Implemented)

Three approaches ranked by impact:

### Approach A: Bridge TickProcessor → EventDetector (Recommended)
- Expose `TickProcessor` signals (velocity_collapse, aggression_shift, absorption) through a callback or shared state so `EventDetector` can emit `PEAK_DETECTION` events from them
- This reuses existing, tested computation without rewriting core math
- Adds ~50 lines of wiring code, zero new detection algorithms

### Approach B: Add Peak Sequence State Machine to PeakDetector
- Track last 4–6 peaks with HH/HL/LH/LL classification
- Detect *failure swings* (HH→LH = bearish reversal potential)
- Increase buffer from 50 to 200+ ticks for meaningful trend context
- Replace raw acceleration with smoothed velocity divergence (e.g., 5-tick EMA)

### Approach C: Rebuild Momentum Divergence with Proper Lookback
- Increase `_rsi_history` lookback from 4 to 14+ data points
- Add classic RSI divergence: price HH + RSI LH over 14+ periods
- Use H1 candles instead of M15 for divergence checks

---

## Validation Strategy

To validate any fix:

1. **Record raw tick data** from a genuine reversal event (e.g., a known pivot on EURUSD)
2. **Replay through current system** — confirm failure (flickering signals, no reversal detected)
3. **Apply fix** — replay same ticks through modified system
4. **Measure**: false positive rate, detection latency, stability (decision change frequency)
5. **Regression**: ensure existing test suite still passes

---

*This report is a diagnosis only. No code has been modified. Awaiting approval for next steps.*
