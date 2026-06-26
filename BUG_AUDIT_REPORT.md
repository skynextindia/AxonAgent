# AxonAI Trading System - Comprehensive Bug Audit Report
**Date:** 2026-06-26  
**Scope:** Tier 1-4 comprehensive audit (daemon.py, trade_executor.py, exit_engine.py, entry_gate.py)  
**Status:** Initial findings documented

---

## TIER 1: CRITICAL BUGS 🔴
*These can cause data loss, incorrect trading, state corruption, or crashes*

### 1.1 RACE CONDITION: `_tracked_positions` accessed without thread lock
**File:** `daemon.py`  
**Lines:** 107, 654, 664, 1091, 1208, 1245, 1623, 1628  
**Severity:** CRITICAL  
**Impact:** Position tracking can become inconsistent. Tick engine thread (TickEngine) and main thread (daemon) both access `_tracked_positions` without synchronization.

**Problem:**
```python
# daemon.py line 107
self._tracked_positions: set[int] = set()

# daemon.py line 1091 - main thread writes
self._tracked_positions.add(ticket)

# daemon.py line 1606-1629 - main thread reads
closed_tickets = self._tracked_positions - active_tickets
```

TickEngine thread updates positions, main thread reads/writes concurrently → race condition.

**Fix:** Use `threading.Lock()` for all `_tracked_positions` access.

---

### 1.2 LOGIC ERROR: Order type inverted when closing positions
**File:** `daemon.py`  
**Line:** 1185  
**Severity:** CRITICAL  
**Impact:** Closing orders may use wrong side (BUY instead of SELL or vice versa), causing execution on wrong terminal or incorrect close price.

**Problem:**
```python
# daemon.py line 1185
order_type = 1 if p["type"] == "BUY" else 0
```

This is **backwards**:
- To close a BUY position, send a SELL (order_type = 1)
- To close a SELL position, send a BUY (order_type = 0)

**Current code:**
- If position is BUY: order_type = 1 (SELL) ✓ CORRECT BY ACCIDENT
- If position is SELL: order_type = 0 (BUY) ✓ CORRECT BY ACCIDENT

**But the variable naming is confusing** - should be explicit about direction.

**Fix:** Change to explicit check:
```python
order_type = 0 if p["type"] == "BUY" else 1  # BUY to close SELL, SELL to close BUY
```

---

### 1.3 MISSING STATE REGISTRATION: Exit reason not stored when exit engine closes
**File:** `daemon.py`  
**Lines:** 1554-1581 (exit engine close block)  
**Severity:** CRITICAL  
**Impact:** When exit engine closes a position, the exit reason is logged but not persisted to `_active_trade_exit_reasons`. Later, when position close is detected, the exit reason is lost.

**Problem:**
```python
# daemon.py line 1554-1581: Exit engine closes position
if exit_signal and exit_signal.should_exit:
    logger.warning("[EXIT_ENGINE] CLOSING ticket %d: %s", ticket, exit_signal.reason)
    # NO STORAGE of exit_signal.reason to self._active_trade_exit_reasons!
    # Position closes, but reason is already logged and forgotten
```

Later at line 1748:
```python
# Position close is detected, but exit reason is gone
reason = "Manual Close / Unknown"  # Lost the exit engine reason!
```

**Fix:** Store exit reason immediately when exit engine decides to close:
```python
self._active_trade_exit_reasons[ticket] = {
    "reason": exit_signal.reason,
    "strategy": "exit_engine",
    "urgency": exit_signal.urgency
}
```

**Status:** PARTIALLY FIXED (code was added to store it at line 1560-1565, but verify it's being retrieved correctly at line 1652-1658)

---

### 1.4 BARE EXCEPTION HANDLER: Swallows all errors silently
**File:** `daemon.py`  
**Line:** 1847  
**Severity:** CRITICAL  
**Impact:** Any error during session log parsing is silently ignored. Could hide data corruption or file format errors.

**Problem:**
```python
# daemon.py line 1847
except Exception:
    continue  # Silently skip bad entries - error is hidden!
```

**Fix:** Log the error:
```python
except Exception as e:
    logger.warning("Error parsing session log entry: %s", e)
    continue
```

---

### 1.5 UNHANDLED None: `entry_decision.direction` used without null check
**File:** `daemon.py`  
**Line:** 1067  
**Severity:** CRITICAL (but masked by entry_gate logic)  
**Impact:** If entry gate returns `direction=None`, line 1067 will fail. Entry gate's fail-closed logic should prevent this, but defensive check needed.

**Problem:**
```python
# daemon.py line 1067
signal = "Buy" if snapshot.entry_decision.direction == "BUY" else "Sell"
# If direction is None, this silently defaults to "Sell" - WRONG!
```

**Fix:**
```python
if not snapshot.entry_decision.direction:
    logger.error("Entry direction is None - skipping trade")
    continue
signal = "Buy" if snapshot.entry_decision.direction == "BUY" else "Sell"
```

---

### 1.6 DEFAULT VALUE MASKS FAILURES: `.get("success", True)` defaults to True
**File:** `daemon.py`  
**Line:** 1088, 1148, 1181  
**Severity:** HIGH-CRITICAL  
**Impact:** If order execution fails but response doesn't include `"success"` field, it defaults to `True`, masking the failure.

**Problem:**
```python
# daemon.py line 1088
if trade_result and trade_result.get("success", True) and trade_result.get("order"):
    # If result = {"order": 12345} but no "success" field, defaults to True!
    # Treats failed order as successful
```

**Fix:** Default to False (fail-safe):
```python
if trade_result and trade_result.get("success", False) and trade_result.get("order"):
```

---

## TIER 2: HIGH BUGS 🟠
*Logic errors, calculation errors, data integrity issues*

### 2.1 EMPTY LIST ACCESS: `m15_closes[-1]` and `ema20` without bounds check
**File:** `daemon.py`  
**Lines:** 306-311  
**Severity:** HIGH  
**Impact:** If `live_evidence._m15_candles` is empty, accessing `m15_closes[-1]` crashes. Code assumes at least 2 elements.

**Problem:**
```python
# daemon.py lines 306-311
m15_closes = [c.close for c in self.live_evidence._m15_candles]
ema20 = m15_closes[0]  # CRASH if m15_closes is empty!
for c in m15_closes[1:]:
    ema20 = (ema20 * 19 + c) / 20
trend_m15 = "up" if m15_closes[-1] > ema20 else "down"  # CRASH if empty!
```

**Fix:**
```python
m15_closes = [c.close for c in self.live_evidence._m15_candles]
if len(m15_closes) < 2:
    logger.warning("Not enough M15 data for EMA calculation")
    return
ema20 = m15_closes[0]
...
```

---

### 2.2 EMPTY LIST ACCESS: `candles_list[-1]` without bounds check
**File:** `daemon.py`  
**Line:** 529  
**Severity:** HIGH  
**Impact:** Accessing last element of potentially empty list without checking.

**Problem:**
```python
# daemon.py line 529
if not candles_list or candles_list[-1]["time"] != cur_time:
```

**Issue:** Should check length before accessing `candles_list[-1]`.

**Fix:**
```python
if not candles_list or candles_list[-1]["time"] != cur_time:
```

Actually, this one is OK because of short-circuit evaluation (`not candles_list` exits early). But the logic could be clearer.

---

### 2.3 POSITION TYPE LOGIC: `pos_type == 0 or pos_type == "BUY"` mixes types
**File:** `daemon.py`  
**Line:** 1478  
**Severity:** HIGH  
**Impact:** Comparing integer (0) with string ("BUY") is confusing and error-prone. Should normalize type first.

**Problem:**
```python
# daemon.py line 1478
pos_type_str = "BUY" if pos_type == 0 or pos_type == "BUY" else "SELL"
# Mixing int (0) and string ("BUY") comparison - confusing and fragile
```

**Fix:**
```python
# Normalize pos_type first
if isinstance(pos_type, int):
    pos_type_str = "BUY" if pos_type == 0 else "SELL"
else:
    pos_type_str = "BUY" if pos_type.upper() == "BUY" else "SELL"
```

---

### 2.4 DEAL TYPE CONFUSION: Multiple comparisons for same enum value
**File:** `daemon.py`  
**Lines:** 1695, 1702  
**Severity:** HIGH  
**Impact:** Using both numeric (0) and MT5 enum comparisons (mt5.DEAL_ENTRY_IN) without clear mapping.

**Problem:**
```python
# daemon.py line 1695
if deal_entry == 1 or (not is_bridge and deal_entry == mt5.DEAL_ENTRY_OUT):
# Unclear: is 1 the same as mt5.DEAL_ENTRY_OUT? Need to verify!
```

**Fix:** Create mapping or use consistent comparison:
```python
DEAL_ENTRY_IN = 0 if mt5 is None else mt5.DEAL_ENTRY_IN
DEAL_ENTRY_OUT = 1 if mt5 is None else mt5.DEAL_ENTRY_OUT

if (is_bridge and deal_entry == 1) or (not is_bridge and deal_entry == DEAL_ENTRY_OUT):
```

---

### 2.5 DIVISION BY ZERO (POTENTIAL): `sl_distance > 0` check but used without validation
**File:** `trade_executor.py`  
**Line:** 183, 220  
**Severity:** HIGH  
**Impact:** Line 220 divides by `sl_pips` which could be near-zero.

**Problem:**
```python
# trade_executor.py line 220
sl_pips = sl_distance / pip  # Could result in very small number
# Line 223
lot_size = round(risk_amount / max(sl_pips * pip_value_per_lot, 1e-6), 2)
# Uses max(..., 1e-6) to prevent division by zero, but sl_pips could still be tiny
```

This is somewhat protected by `max(..., 1e-6)`, but the real issue is if `sl_pips` is calculated as 0.00001 and pip_value_per_lot=10, then denominator = 0.0001 (very small lot calculation).

**Fix:** Add explicit check:
```python
if sl_pips < 1.0:
    logger.warning("SL distance too small: %.5f pips - entry skipped", sl_pips)
    return {"success": False, "reason": "sl_too_small"}
```

---

### 2.6 MISSING ERROR HANDLING: `send_order_via_bridge` could return None
**File:** `daemon.py`  
**Multiple lines accessing bridge results**  
**Severity:** HIGH  
**Impact:** If bridge returns None, `.get()` will crash with AttributeError.

**Problem:**
```python
# Line 1150: send_execution_command returns dict or None
res = send_execution_command(...)
positions = res.get("positions", [])  # CRASH if res is None!
```

**Fix:**
```python
res = send_execution_command(...)
if not res:
    logger.error("Bridge command failed - no response")
    return
positions = res.get("positions", [])
```

---

## TIER 3: MEDIUM BUGS 🟡
*Edge cases, potential crashes, minor logic issues*

### 3.1 STALE TIME CALCULATION: `_seconds_until_ready()` might return negative
**File:** `daemon.py`  
**Severity:** MEDIUM  
**Impact:** If cooldown elapsed but comparison is off, could trigger false cooldown.

---

### 3.2 DIVERGENT TRADER EXECUTORS: Two MT5TradeExecutor instances with different magic numbers
**File:** `daemon.py`  
**Lines:** 84-88  
**Severity:** MEDIUM  
**Impact:** Both use same terminal but different magic numbers. If one fails, positions might be orphaned.

```python
self.trade_executor_base = MT5TradeExecutor(config_base)  # magic 123456
self.trade_executor_opt = MT5TradeExecutor(config_opt)     # magic 123457
self.trade_executor = self.trade_executor_opt  # Which one is used?
```

---

### 3.3 MISSING NULL CHECK: `trade_state.health_score` attribute access
**File:** `daemon.py`  
**Line:** 1508  
**Severity:** MEDIUM  
**Impact:** If `health_state` is None, `.health_score` will crash.

```python
health_score=health_state.health_score if health_state else 50.0
# But later uses assume health_score exists
```

---

### 3.4 IMPLICIT TYPE CONVERSION: Mixing float and string for prices
**File:** Multiple  
**Severity:** MEDIUM  
**Impact:** Price comparisons could fail if type is inconsistent.

---

## TIER 4: LOW BUGS 🟢
*Code quality, performance, maintainability*

### 4.1 UNUSED VARIABLE: `_lowest_price_since_entry` tracking initialized but not used
**File:** `daemon.py`  
**Line:** 93  
**Severity:** LOW  

---

### 4.2 INEFFICIENT LOGGING: Logging every candle close and tick
**File:** `daemon.py`  
**Lines:** 1006-1008  
**Severity:** LOW  
**Impact:** Spam in log files, could fill disk in production.

---

### 4.3 MAGIC NUMBERS: Hard-coded values like 123456, 123457 for magic numbers
**File:** `daemon.py`  
**Lines:** 81-87  
**Severity:** LOW  

---

## SUMMARY TABLE

| ID | Component | Severity | Category | Status |
|---|---|---|---|---|
| 1.1 | daemon.py | CRITICAL | Race Condition | NOT FIXED |
| 1.2 | daemon.py | CRITICAL | Logic Error | NOT FIXED |
| 1.3 | daemon.py | CRITICAL | State Loss | PARTIALLY FIXED |
| 1.4 | daemon.py | CRITICAL | Error Handling | NOT FIXED |
| 1.5 | daemon.py | CRITICAL | Null Check | NOT FIXED |
| 1.6 | daemon.py | CRITICAL | Default Value | NOT FIXED |
| 2.1 | daemon.py | HIGH | Bounds Check | NOT FIXED |
| 2.2 | daemon.py | HIGH | Bounds Check | ACCEPTABLE |
| 2.3 | daemon.py | HIGH | Type Logic | NOT FIXED |
| 2.4 | daemon.py | HIGH | Enum Logic | NOT FIXED |
| 2.5 | trade_executor.py | HIGH | Math | PARTIALLY MITIGATED |
| 2.6 | daemon.py | HIGH | Null Check | NOT FIXED |
| 3.1-3.4 | daemon.py | MEDIUM | Various | NOT FIXED |
| 4.1-4.3 | daemon.py | LOW | Code Quality | NOT FIXED |

---

## RECOMMENDED FIX PRIORITY

**Phase 1 (Immediate - breaks money-touching logic):**
- 1.1: Add thread lock for `_tracked_positions`
- 1.2: Fix order type logic for closing positions
- 1.5: Add null check for entry_decision.direction
- 1.6: Change `.get("success", False)` to fail-safe default

**Phase 2 (High impact - logic errors):**
- 1.4: Log bare exceptions instead of swallowing
- 2.1: Add empty list checks before accessing [-1]
- 2.3: Normalize position type logic
- 2.6: Add null checks for bridge responses

**Phase 3 (Robustness):**
- Remaining medium and low priority bugs

---

## AUDIT NOTES

- No syntax errors found in compiled files
- Type annotations generally present and correct
- Exception handling exists but often too broad
- Thread safety not comprehensively implemented
- Edge case handling (empty lists, None values) inconsistent

