# Design: Dynamic Z-Score Adaptive Displacement Classification

**Date:** 2026-06-25  
**Problem:** Static displacement thresholds (0.60 impulse, 0.25 trap) fail across varying market conditions. Asia sessions (choppy, 0.23-0.29 ratio) reject valid reversals; London/NY (clean, 0.50+) accept genuine impulses. System misses profitable entries because thresholds don't adapt.

**Solution:** Replace fixed ratio thresholds with z-score metrics that adapt to rolling 5-minute market conditions.

---

## 1. Problem Statement

### Current Behavior
- **Impulse threshold:** Fixed at displacement_ratio ≥ 0.60
- **Trap threshold:** Fixed at displacement_ratio < 0.25
- **Neutral gap:** 0.25-0.60 (everything in-between)

### Why It Fails

| Session | Avg Ratio | Typical Range | Issue |
|---------|-----------|---------------|-------|
| **Asia** | 0.35 | 0.20-0.40 | Clean reversals at 0.23-0.29 rejected as TRAP |
| **London** | 0.55 | 0.45-0.65 | Genuine impulses at 0.50+ accepted |
| **NY Open** | 0.60 | 0.55-0.70 | Cleanest moves, threshold fits |

**Result:** Entry state machine never triggers in Asia because displacement stays NEUTRAL/TRAP. User observes reversals but system doesn't trade.

---

## 2. Solution: Z-Score Adaptive Classification

### Core Insight
Instead of asking "Is displacement_ratio > 0.60?", ask "Is this displacement ratio unusual compared to recent conditions?"

### Mechanism

**Current (static):**
```python
if displacement_ratio >= 0.60:
    return DISPLACEMENT_IMPULSE
elif displacement_ratio < 0.25:
    return DISPLACEMENT_TRAP
else:
    return DISPLACEMENT_NEUTRAL
```

**New (adaptive):**
```python
displacement_z_score = (displacement_ratio - rolling_mean) / rolling_stdev

if displacement_z_score >= 1.5:
    return DISPLACEMENT_IMPULSE
elif displacement_z_score <= -1.5:
    return DISPLACEMENT_TRAP
else:
    return DISPLACEMENT_NEUTRAL
```

### Why Z-Score Works

- **Self-calibrating** — System learns what's "normal" for current market state
- **Regime-agnostic** — Works identically in choppy Asia (mean=0.35) and clean London (mean=0.55)
- **Statistically sound** — Z>1.5 means movement is 1.5σ above mean = genuinely unusual
- **No magic constants** — Single threshold (1.5) works across all volatility regimes

### Example: Asia Session (Choppy)
```
Recent displacement ratios (5 min): [0.21, 0.28, 0.35, 0.29, 0.32, 0.25...]
Rolling mean = 0.28
Rolling stdev = 0.05

New tick displacement = 0.40
z-score = (0.40 - 0.28) / 0.05 = 2.4σ above mean
Result: IMPULSE ✓ (despite ratio=0.40 < static threshold 0.60)
```

### Example: London Session (Clean)
```
Recent displacement ratios (5 min): [0.52, 0.58, 0.61, 0.55, 0.59...]
Rolling mean = 0.57
Rolling stdev = 0.03

New tick displacement = 0.50
z-score = (0.50 - 0.57) / 0.03 = -2.3σ below mean
Result: TRAP ✓ (despite ratio=0.50 > static trap threshold 0.25)
```

---

## 3. Implementation Architecture

### 3.1 New Component: DisplacementNormalizer

**Purpose:** Compute z-score for displacement ratios (parallel to VelocityNormalizer for velocity)

**Location:** `axonai/realtime/displacement_normalizer.py` (new file)

**Interface:**
```python
class DisplacementNormalizer:
    def __init__(self, window_sec: float = 300.0):
        """
        Args:
            window_sec: Rolling time window (default 300s = 5 minutes)
        """
        
    def update(self, displacement_ratio: float, timestamp: float) -> NormalizedDisplacement:
        """
        Process one displacement ratio, return z-score and metrics.
        
        Returns:
            NormalizedDisplacement(
                ratio=0.40,
                z_score=1.5,           # NEW: (ratio - mean) / stdev
                percentile=75.0,       # rank in rolling window
                rolling_mean=0.28,
                rolling_stdev=0.05,
                sample_count=142
            )
        """
```

**Key Metrics:**
- `rolling_mean` — Average displacement ratio over 5-minute window
- `rolling_stdev` — Standard deviation of recent ratios
- `z_score` — (current_ratio - mean) / stdev
- `sample_count` — Number of ticks in window (validate min=50 before using z-score)

**Data Structure:**
- Deque of (ratio, timestamp) tuples, maxlen = large enough for 5 min of ticks (typically 200-500)
- Running sum/sum_sq for efficient Welford variance calculation

### 3.2 Modified: DisplacementEngine._classify()

**Current logic (lines 207-247):**
```python
def _classify(self, velocity, disp_ratio, net_move, total_move) -> str:
    # Priority 1: Exhaustion
    if is_decaying and total_move > 3.0:
        return DISPLACEMENT_EXHAUSTION
    
    # Priority 2: Impulse (HIGH VEL + HIGH DISPLACEMENT)
    if is_high_vel and disp_ratio >= self._impulse_threshold:  # 0.60
        return DISPLACEMENT_IMPULSE
    
    # Priority 3: Trap (HIGH VEL + LOW DISPLACEMENT)
    if is_high_vel and disp_ratio < self._trap_threshold:  # 0.25
        return DISPLACEMENT_TRAP
    
    # ...rest
```

**New logic:**
```python
def _classify(self, velocity, disp_ratio, net_move, total_move, disp_z_score) -> str:
    # Priority 1: Exhaustion (unchanged)
    if is_decaying and total_move > 3.0:
        return DISPLACEMENT_EXHAUSTION
    
    # Priority 2: Impulse (ADAPTIVE: z-score instead of fixed ratio)
    if is_high_vel and disp_z_score >= 1.5:  # CHANGED
        return DISPLACEMENT_IMPULSE
    
    # Priority 3: Trap (ADAPTIVE: z-score instead of fixed ratio)
    if is_high_vel and disp_z_score <= -1.5:  # CHANGED
        return DISPLACEMENT_TRAP
    
    # ...rest
```

**Changes:**
- Add `disp_z_score` parameter (injected from DisplacementNormalizer)
- Replace `disp_ratio >= self._impulse_threshold` with `disp_z_score >= 1.5`
- Replace `disp_ratio < self._trap_threshold` with `disp_z_score <= -1.5`
- Keep all other logic unchanged (exhaustion, compression, neutral)

### 3.3 Integration: daemon.py

**Location:** `axonai/realtime/daemon.py` in `_update_state()` method

**Current flow:**
```python
velocity = self.velocity_normalizer.update(price, ts, volume)
displacement = self.displacement_engine.update(price, ts, volume, velocity)
```

**New flow:**
```python
velocity = self.velocity_normalizer.update(price, ts, volume)
disp_raw = self.displacement_engine.update(price, ts, volume, velocity)

# NEW: Compute z-score normalization
disp_norm = self.displacement_normalizer.update(disp_raw.displacement_ratio, ts)

# NEW: Inject z-score back into classification
displacement = self.displacement_engine._reclassify(disp_raw, disp_norm.z_score)
```

**Alternative (cleaner):** Refactor DisplacementEngine to accept normalizer instance:
```python
displacement = self.displacement_engine.update(
    price, ts, volume, velocity, 
    normalizer=self.displacement_normalizer  # NEW param
)
```

---

## 4. Z-Score Thresholds

### Primary Thresholds
| Classification | Z-Score | Meaning |
|---|---|---|
| **IMPULSE** | z ≥ 1.5 | Displacement 1.5σ above recent mean |
| **TRAP** | z ≤ -1.5 | Displacement 1.5σ below recent mean |
| **NEUTRAL** | -1.5 < z < 1.5 | Normal variation, context-dependent |

### Rationale for 1.5σ
- **Standard practice:** 1σ = 68% of data (too permissive), 2σ = 95% (too strict), 1.5σ ≈ 87% (sweet spot)
- **Conservative:** Requires unusual-but-not-extreme movement
- **Handles noise:** Filters out minor variations while catching real regime shifts

### Safety Rails (Soft Bounds)

**Optional constraint:** Warn if z-threshold drifts significantly from static baseline.
```python
impulse_z_threshold = 1.5
if rolling_mean > 0.50:  # Session is clean
    # London/NY behavior: static threshold (0.60) naturally close to z-score behavior
    pass
elif rolling_mean < 0.30:  # Session is choppy
    # Asia behavior: z-score adapts down, but log it
    logger.debug(f"DisplacementEngine: Choppy session detected (mean={rolling_mean:.3f})")
```

**No hard bounds needed** — Z-score naturally self-limits because if choppy session means=0.30, even a "high" ratio of 0.50 is only ~3σ above mean, so system won't over-adapt.

---

## 5. Rolling Window: 5 Minutes (300 seconds)

### Why 5 Minutes?

| Window | Pro | Con |
|--------|-----|-----|
| **1 min** | Very responsive | Too jittery, reacts to every spike |
| **5 min** | ✓ Balances responsiveness + stability | Slight lag in session transitions |
| **10 min** | Smooth | Too slow for Asia→London transition |

### Implementation

**Time-based, not tick-based:**
```python
now_ts = timestamp.timestamp()
cutoff_ts = now_ts - 300.0  # 5 minutes ago

# Keep only ratios from last 5 minutes
active_ratios = [(r, t) for r, t in self._history if t >= cutoff_ts]

# Calculate mean/stdev on active_ratios
rolling_mean = sum(r for r, t in active_ratios) / len(active_ratios)
```

### Bootstrap Constraint

**Minimum sample requirement:** Don't use z-score if fewer than 50 ticks in 5-min window.
```python
if len(active_ratios) < 50:
    # Insufficient data, fall back to static thresholds
    use_static_thresholds = True
else:
    # Enough data, use z-score
    z_score = (ratio - rolling_mean) / rolling_stdev
    use_z_score = True
```

**Rationale:** Session open or low-liquidity periods might have sparse ticks. With <50 ticks, z-score is unreliable.

---

## 6. Data Flow Diagram

```
Live Tick
  │
  ├─→ VelocityNormalizer.update()
  │   └─→ NormalizedVelocity (z_score, is_unusual, etc.)
  │
  ├─→ DisplacementEngine.update()
  │   ├─→ Calculate displacement_ratio
  │   ├─→ DisplacementNormalizer.update(ratio, ts)  [NEW]
  │   │   └─→ NormalizedDisplacement (z_score, mean, stdev)
  │   └─→ _classify(velocity, ratio, z_score)  [MODIFIED]
  │       └─→ DisplacementState (classification=IMPULSE|TRAP|NEUTRAL)
  │
  └─→ EntryStateMachine.evaluate()
      ├─→ _evaluate_idle() — Detect anomalies
      ├─→ _evaluate_anomaly() — Monitor displacement
      └─→ _evaluate_arming() — Check is_impulse = (z_score >= 1.5)
          └─→ EntryDecision (is_valid_entry, direction, quality)
```

---

## 7. Testing Strategy

### Unit Tests: DisplacementNormalizer
```python
def test_displacement_normalizer_z_score_calculation():
    """Verify z-score = (ratio - mean) / stdev"""
    
def test_displacement_normalizer_moving_window():
    """Verify only 5-min window of ratios is used"""
    
def test_displacement_normalizer_bootstrap_constraint():
    """With <50 ticks, z_score is None or marked invalid"""
```

### Integration Tests: DisplacementEngine + Normalizer
```python
def test_adaptive_impulse_detection_choppy_market():
    """Asia session (mean=0.30): ratio=0.50 should be IMPULSE"""
    
def test_adaptive_impulse_detection_clean_market():
    """London session (mean=0.55): ratio=0.50 should be TRAP"""
    
def test_exhaustion_still_works():
    """Exhaustion classification unaffected by z-score changes"""
```

### Live Market Tests
```
Before:  Asia session, EURUSD 1.13742 reversal → NEUTRAL (rejected)
After:   Same reversal → IMPULSE (accepted, trades)

Before:  London session, false breakouts → IMPULSE (2 losses)
After:   Same breakouts → NEUTRAL (protected)
```

---

## 8. Configuration & Tuning

### Exposed Settings (default_config.py)
```python
"displacement_z_score_impulse_threshold": 1.5,
"displacement_z_score_trap_threshold": -1.5,
"displacement_rolling_window_sec": 300.0,
"displacement_bootstrap_min_ticks": 50,
```

### Optional: Session-Aware Tuning
```python
# If needed, user can override:
if current_session == "ASIA":
    impulse_threshold = 1.3  # More aggressive
elif current_session == "LONDON":
    impulse_threshold = 1.7  # More conservative
```

---

## 9. Rollback Plan

If z-score adaptation causes issues (e.g., over-adaptation in extreme volatility):

1. **Quick disable:** Set `impulse_threshold` back to 0.60 in config (falls back to static behavior)
2. **Gradual revert:** Use hybrid — `if z_score available, use z-score; else use ratio`
3. **Full revert:** Remove DisplacementNormalizer, revert DisplacementEngine._classify()

**Effort:** <5 minutes (single config change)

---

## 10. Success Criteria

✅ **Asia reversals now trigger entries:** Displacement 0.23-0.29 classified as IMPULSE (z>1.5)  
✅ **London precision maintained:** Displacement 0.50 correctly classified based on market mean  
✅ **Exhaustion still works:** Velocity decay + any displacement = EXHAUSTION (unchanged)  
✅ **No false positives:** >1.5σ threshold prevents noise-driven entries  
✅ **Responsive:** 5-min window adapts to session changes within 2-3 min  
✅ **Tests pass:** All existing displacement tests + 3 new z-score tests green  

---

## 11. Timeline

- **Phase 1:** Implement DisplacementNormalizer (1-2 hrs)
- **Phase 2:** Integrate into DisplacementEngine._classify() (30-45 min)
- **Phase 3:** Wire into daemon.py (15 min)
- **Phase 4:** Write unit + integration tests (1-2 hrs)
- **Phase 5:** Dry-run testing in Asia session (30 min)
- **Phase 6:** Live validation (ongoing monitoring)

**Total:** 4-6 hours to full deployment

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Over-adaptation** in extreme vol | False entries in choppy news events | Require z>1.5 (conservative), not >1.0 |
| **Lag at session open** | Cold start with sparse data | Min 50 ticks before using z-score |
| **Floating mean** in trends | Slow drift confuses classification | 5-min window auto-resets, no drift accumulation |
| **Broken backtest compatibility** | Can't replay old results | New component is daemon-only, backtest unaffected |

---

## Appendix: Code Outline

### DisplacementNormalizer (new file)
```python
@dataclass
class NormalizedDisplacement:
    ratio: float = 0.0
    z_score: float = 0.0
    percentile: float = 50.0
    rolling_mean: float = 0.0
    rolling_stdev: float = 0.0
    sample_count: int = 0

class DisplacementNormalizer:
    def __init__(self, window_sec: float = 300.0):
        self._window_sec = window_sec
        self._history: deque[(float, float)] = deque()  # (ratio, ts)
        self._sum = 0.0
        self._sum_sq = 0.0
    
    def update(self, ratio: float, ts: float) -> NormalizedDisplacement:
        # Prune old samples
        cutoff = ts - self._window_sec
        while self._history and self._history[0][1] < cutoff:
            old_ratio, _ = self._history.popleft()
            self._sum -= old_ratio
            self._sum_sq -= old_ratio * old_ratio
        
        # Add new sample
        self._history.append((ratio, ts))
        self._sum += ratio
        self._sum_sq += ratio * ratio
        
        # Calculate stats
        n = len(self._history)
        mean = self._sum / n if n > 0 else 0.0
        var = (self._sum_sq / n) - (mean ** 2) if n > 50 else 0.0
        stdev = math.sqrt(max(0.0, var))
        z_score = (ratio - mean) / stdev if stdev > 0 else 0.0
        
        return NormalizedDisplacement(
            ratio=round(ratio, 4),
            z_score=round(z_score, 2),
            rolling_mean=round(mean, 4),
            rolling_stdev=round(stdev, 4),
            sample_count=n
        )
```

---

**End of Design Document**
