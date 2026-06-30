# Velocity-Based Trailing Stop System

## Overview

The Velocity Trailing system dynamically manages stop losses in real-time based on market structure signals rather than fixed profit thresholds.

**Problem Solved:**
- Traditional trailing waits for arbitrary profit levels (e.g., 20 pips)
- For EURUSD with 20 pip SL, waiting 20 pips is ineffective
- By the time you have full risk exposure, trailing is too late

**Solution:**
- Trail SL when velocity ACCELERATES (20%+ speed increase)
- Trail when price RETESTS SL area (confirmed as support)
- Respond to live market conditions, not fixed thresholds

---

## System Architecture

### Tier 1: Market Data Inputs
```
Velocity Percentile    → Current price movement speed (0-100)
Velocity Acceleration  → Is price getting faster? (1.0=unchanged, 1.25=25% faster)
Displacement Ratio     → Is movement trending or choppy? (0-1)
Health Score          → Trade thesis confidence (0-100)
Lowest Price          → Minimum price since entry (for retest detection)
```

### Tier 2: Real-Time Detection

**Velocity Acceleration Detection:**
```python
velocity_accel = current_velocity / previous_velocity
if velocity_accel >= 1.2:  # 20% faster
    → ACCELERATION DETECTED
    → Boost trailing aggressiveness
```

**Retest Detection:**
```python
if price_within_3_pips_of_SL and price_bouncing_back_up:
    → RETEST CONFIRMED
    → SL is now support level
    → Trail immediately
```

### Tier 3: Aggressiveness Calculation

Not fixed percentages. Dynamically calculated:

```
base_aggressiveness = (
    velocity_score × 0.35 +        # How fast is price moving?
    displacement × 0.35 +          # Is it trending or choppy?
    health_score / 100 × 0.3       # How confident is the thesis?
)

final_agg = base_agg × velocity_acceleration × retest_boost
```

**Example:**
```
Velocity: 60th percentile → 0.6 score
Displacement: 0.4 ratio → 0.4 score
Health: 80% → 0.8 score
Velocity Acceleration: 1.3 (30% faster) → 1.3 multiplier
Retest: YES → 1.3 boost

agg = (0.6×0.35 + 0.4×0.35 + 0.8×0.3) × 1.3 × 1.3
    = 0.595 × 1.3 × 1.3 = 1.0 (MAX AGGRESSIVE)
```

### Tier 4: Trail Distance

Based on current profit + aggressiveness:

```python
trail_distance = 5.0 pips × (1.0 - agg × 0.7) × (1.0 - profit_factor × 0.5)

# Examples for your trades:
agg=0.3, profit=5pips  → 4.2 pips buffer from current price
agg=0.7, profit=15pips → 1.8 pips buffer from current price
agg=1.0, profit=30pips → 0.5 pips buffer from current price
```

---

## Trail Speed (Frequency of SL Updates)

Based on aggressiveness:

```
agg >= 0.8  → Every 3 ticks (300ms)   [AGGRESSIVE]
agg >= 0.5  → Every 10 ticks (1.0s)   [NORMAL]
agg <  0.5  → Every 25 ticks (2.5s)   [CONSERVATIVE]
```

---

## Three Conditions to Update SL

**ALL must be true:**

1. **Velocity Acceleration OR Retest Detected**
   ```
   velocity_accel >= 1.2  OR  price_tested_SL_and_bounced
   ```

2. **Price Moving Away From SL**
   ```
   distance_from_SL >= 2.0 pips
   ```

3. **Thesis Still Intact**
   ```
   health_score >= 50.0
   ```

**Example Flow:**
```
Entry: 1.13511, SL: 1.13311

Price: 1.13510 (profit: -0.1 pips) → No trail (losing)

Price: 1.13512 (profit: +0.1 pips) → Velocity normal, no acceleration → No trail

Price: 1.13525 (profit: +1.4 pips) → Velocity 45→58 percentile (28% acceleration) → TRAIL!
  New SL: 1.13325 (lock 1.4 pips)

Price: 1.13520 (profit: +0.9 pips) → Retest SL area, velocity 58→72 (24% accel) → TRAIL!
  New SL: 1.13330 (re-lock)

Price: 1.13540 (profit: +2.9 pips) → Velocity 72→88 (22% accel), strong trend → TRAIL!
  New SL: 1.13340 (lock 2.9 pips)
```

---

## Configuration

In `axonai/default_config.py`:

```python
"realtime_dry_run": False,              # Enable live auto-entry
"realtime_enabled": True,               # Enable real-time engine

# Velocity trailing thresholds
# (in velocity_trailing.py)
velocity_acceleration_threshold = 1.2   # 20% faster = acceleration
retest_window_pips = 3.0               # Price within 3 pips of SL = testing
min_price_distance_to_trail = 2.0      # Minimum 2 pips away from SL to trail
max_trail_distance = 15.0              # Never trail more than 15 pips
```

---

## Integration Points

**daemon.py (_manage_trailing_stops):**
- Receives velocity_percentile, velocity_acceleration, displacement_ratio, health_score, lowest_price
- Calls velocity_trailing.on_tick() every tick
- Updates SL via MT5 order_send() when trail triggered
- Logs all trail modifications with aggressiveness metrics

**velocity_trailing.py:**
- Core algorithm: detects acceleration, retests, calculates aggressiveness
- on_tick(): receives market data, returns {new_sl, reason, aggressiveness} or None
- reset(): clears state when position closes

---

## What the Logs Show

```
[INFO] VelocityTrail BUY #57245085689: 
       SL 1.13286 -> 1.13350 
       (agg=0.78, accel=1.28, retest=True)

Meaning:
- Ticket #57245085689 (BUY position)
- SL moved from 1.13286 to 1.13350 (64 pips trail)
- Aggressiveness: 0.78 (NORMAL mode, check every 10 ticks)
- Velocity acceleration: 1.28 (28% faster than last check)
- Retest: True (price tested SL area and bounced back)
```

---

## Live Monitoring

**Dashboard TRADE_STATE Panel:**
- PHASE: Current lifecycle phase (ENTRY → EXPANSION → ... → EXIT)
- HEALTH%: Trade thesis confidence
- MFE: Max favorable excursion (highest price reached)
- MAE: Max adverse excursion (lowest price reached)
- PROFIT: Current P&L in pips

**As SL trails:**
- MFE increases (new high prices locked in)
- PROFIT locked = New SL price - Entry price

---

## Differences from Traditional Trailing

| Aspect | Traditional | Velocity Trailing |
|--------|---|---|
| Start Condition | Fixed profit threshold | Velocity acceleration + retest |
| Update Frequency | Every X ticks | Every 3-25 ticks (adaptive) |
| Distance | Fixed % of risk | Dynamic based on aggressiveness |
| Market Response | Slow (waits for threshold) | Fast (real-time) |
| Retest Handling | N/A | Confirms SL as support |
| EURUSD Efficiency | Low (waits 20 pips) | High (trails from +1 pip) |

---

## Best Practices

1. **Monitor velocity_acceleration in logs** - Watch for 20%+ spikes
2. **Retest detection is key** - When SL is tested and holds, it's confirmed support
3. **Health score matters** - Don't trail if thesis is breaking down (<50%)
4. **Adjust min_price_distance_to_trail per instrument:**
   - EURUSD (tight spread): 1.5-2.0 pips
   - Gold (wide spread): 3-5 pips
   - Stocks: 5-10 pips

---

## Example Trade Walkthrough

**Entry: BUY at 1.13511, SL: 1.13311, TP: 1.13911**

```
TICK 100 (10 seconds):
  Price: 1.13512
  Profit: +0.1 pips
  Velocity: 45th percentile, accel=1.1
  Status: Too early, no acceleration

TICK 150 (15 seconds):
  Price: 1.13525
  Profit: +1.4 pips
  Velocity: 62nd percentile, accel=1.38 (38% faster!)
  Retest: NO
  Action: TRAIL! (acceleration detected)
  New SL: 1.13325
  Profit locked: +1.4 pips

TICK 200 (20 seconds):
  Price: 1.13518 (pullback)
  Profit: +0.7 pips
  Velocity: 58th percentile, accel=0.94 (slowing down)
  Retest: YES (price within 3 pips of 1.13325)
  Action: TRAIL! (retest detected, SL holds as support)
  New SL: 1.13328
  Profit locked: +0.7 pips

TICK 250 (25 seconds):
  Price: 1.13545
  Profit: +3.4 pips
  Velocity: 78th percentile, accel=1.35
  Retest: NO
  Action: TRAIL! (strong acceleration)
  New SL: 1.13340
  Profit locked: +2.9 pips

... continues until exit or stop loss hit
```

---

## Testing

Run the demo:
```bash
python demo_velocity_trailing.py
```

Run a live test:
```bash
python test_live_entry.py
```

Monitor daemon logs:
```bash
grep "VelocityTrail" daemon_run.out
```
