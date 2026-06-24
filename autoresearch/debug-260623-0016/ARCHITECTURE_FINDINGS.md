# AxonAgent Order Execution Architecture — Investigation Summary

## Executive Summary

Orders execute through a complex pipeline: **Tick → EntryStateMachine → EventQueue → Executor → MT5/Bridge**. Why orders aren't executing today: either (1) the entry state machine never reaches TRIGGERED state, or (2) execution is configured for wrong mode.

---

## Architecture Overview

### Layer 1: Daemon Initialization (daemon.py:529-612)

```
AxonDaemon.start()
├─ MT5 initialization (mt5_initialize)
├─ Check realtime_execution_mode ("direct" or "bridge")
├─ Cold-start LiveWorldState from historical bars
├─ Start TickEngine thread (feeds live ticks)
├─ Register dashboard callbacks
└─ Enter _event_loop()
```

**Configuration Check:**
- `config.get("realtime_execution_mode", "direct")` — **defaults to "direct"**
- ⚠️ **NOT DEFINED in default_config.py** — must be set explicitly or bridge won't work

### Layer 2: Real-Time Event Detection (_on_tick, daemon.py:686-702)

On every tick:
1. **LiveState** updates bid/ask/timestamp
2. **ReversalModel** processes tick through entry state machine
3. **EntryStateMachine** evaluates 5-state transitions:
   - **IDLE** → awaiting anomaly (climax or sweep)
   - **ANOMALY** → anomaly detected, waiting for absorption
   - **ARMING** → trap confirmed, waiting for impulse
   - **TRIGGERED** → ✓ **valid entry signal** (is_valid_entry=True)
   - **INVALIDATED** → anomaly broken or timeout
4. If **state == TRIGGERED** → event queued with type="entry"

### Layer 3: Event Processing (_event_loop, daemon.py:826-905)

Main loop blocks on queue:
```
while running:
  event = queue.get(timeout=1.0)
  
  if event["type"] == "entry":
    snapshot = event["snapshot"]
    signal = "Buy" if entry_decision.direction=="BUY" else "Sell"
    
    # Check cooldown, paused state, trading hours
    if valid:
      trade_result = trade_executor_opt.execute_signal(symbol, signal, live_state)
```

**Gating Conditions** (before execution):
- ✓ Trading not paused
- ✓ Cooldown elapsed (300s default)
- ✓ Event priority met

### Layer 4: Trade Execution (trade_executor.py:79-242)

**execute_signal()** → **send_order()** → **Route by Mode**

#### Direct Mode (realtime_execution_mode == "direct")
```python
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": BUY or SELL,
    "price": ask (BUY) or bid (SELL),
    "sl": entry ± SL_distance,
    "tp": entry ± TP_distance,
    "deviation": 20,
    "magic": magic_number
}
result = mt5.order_send(request)  # Requires active MT5 terminal
```

#### Bridge Mode (realtime_execution_mode == "bridge")
```python
result = send_execution_command(config, {
    "action": "open",
    "symbol": symbol,
    "type": BUY (0) or SELL (1),
    "volume": lot,
    "price": entry,
    "sl": sl,
    "tp": tp,
    "magic": magic_number,
    "deviation": 20
})
# WebSocket to ws://127.0.0.1:8766 (execution_bridge.py)
```

---

## Critical Files & Decision Points

| Component | File | Key Lines | Purpose |
|-----------|------|-----------|---------|
| **Daemon** | daemon.py | 529-612 | Initialize MT5, start tick engine, enter event loop |
| **Tick Processor** | daemon.py | 686-702 | Process ticks, detect entry signals |
| **Entry Gate** | entry_state_machine.py | 81-235 | 5-state machine: IDLE→ANOMALY→ARMING→TRIGGERED |
| **Executor** | trade_executor.py | 53-298 | Route to direct MT5 or execution bridge |
| **Execution Client** | execution_client.py | 39-74 | Send WebSocket commands to bridge |
| **Bridge Server** | windows/execution_bridge.py | 1-100+ | Listens on port 8766, relays orders to MT5 |
| **Config** | axonai/default_config.py | 1-170 | ⚠️ Missing `realtime_execution_mode` definition |

---

## Entry State Machine Transitions

### State 1: IDLE → ANOMALY
**Trigger:** Microstructure anomaly detected
- **Climax:** `velocity.is_unusual AND tick_efficiency < 0.2`
- **Sweep:** Active liquidity sweep in liq.active_sweeps

### State 2: ANOMALY → ARMING
**Trigger:** Trap or velocity decay
- `displacement.classification IN (TRAP, ABSORPTION)`
- `velocity.is_decaying == True`

### State 3: ARMING → TRIGGERED ✓
**Trigger:** Genuine displacement impulse (>1.5 pips) away from trap
- Direction check: BUY if dist > 1.5, SELL if dist < -1.5
- No strong trend block (H1 & H4 bias checks)

### Invalidation Points ✗
- Anomaly reversed too far (MAE > 5 pips) without absorption
- Strong trend blocks entry (H1 bias < -0.4 or H4 bias < -0.4)
- Timeout (120 seconds default)

---

## Why Orders Might Not Be Executing

### Hypothesis 1: Configuration Issue
- `realtime_execution_mode` not set → defaults to "direct"
- Direct mode requires MT5 terminal running locally
- **If MT5 not connected:** mt5.order_send() fails silently or returns error

### Hypothesis 2: Entry Signal Never Triggered
- No microstructure anomalies in market today
- Or anomalies invalidated before reaching TRIGGERED state
- Entry state machine stuck in ANOMALY or ARMING

### Hypothesis 3: Bridge Service Not Running
- If bridge mode enabled, execution_bridge.py not running on port 8766
- send_execution_command() connects to ws://127.0.0.1:8766
- **If bridge down:** WebSocket connection error → order fails

### Hypothesis 4: Cooldown or Gating
- Trading paused (daemon.paused = True)
- Cooldown active (300s default)
- Risk guard tripped (circuit breaker)

---

## Velocity & Displacement Requirements

For entry signal to trigger, system needs:

1. **Velocity Metrics** (velocity_normalizer.py):
   - `is_unusual` = percentile > 90 OR z_score > 2.0
   - `is_decaying` = decay_ratio < 0.5 AND peak_decay_ticks > 10 (backtest) or 3 (live)
   - `tick_efficiency` < 0.2 (for climax detection)

2. **Displacement Metrics** (displacement_engine.py):
   - Net displacement > 1.5 pips in entry direction
   - Classification: IMPULSE, TRAP, or ABSORPTION

3. **Market Conditions**:
   - Liquidity sweeps detected, OR
   - Climax exhaustion visible, OR
   - High-velocity absorption forming

---

## Configuration Gaps

**Missing from default_config.py:**
- `realtime_execution_mode` — should be "direct" or "bridge"
- Execution bridge host/port for bridge mode

**Implied Defaults:**
- Bridge disabled (defaults to "direct")
- Direct mode requires MT5 terminal connection
- Bridge would listen on port 8766 if enabled

---

## Execution Bridge Service

**File:** windows/execution_bridge.py

**Startup Command:**
```bash
python windows/execution_bridge.py --port 8766 --path "C:\Program Files\MetaTrader 5\terminal64.exe"
```

**What it does:**
- Initializes MT5 on separate terminal
- Listens on WebSocket at ws://127.0.0.1:8766
- Routes "open", "modify", "close", "positions_get" actions to MT5
- Returns order results back to daemon

---

## Next Steps to Debug

1. **Check Current Execution Mode:**
   - Verify config file — is `realtime_execution_mode` set?
   - If not, add it to config

2. **If Direct Mode:**
   - Verify MT5 terminal running with correct account
   - Check mt5.terminal_info() returns valid connection
   - Monitor daemon.py logs for order_send() failures

3. **If Bridge Mode:**
   - Start execution_bridge.py service
   - Verify WebSocket listening on port 8766
   - Check daemon logs for connection errors

4. **Check Entry Signal Generation:**
   - Monitor entry_state_machine transitions in logs
   - Check if TRIGGERED state ever reached
   - Review velocity, displacement, liquidity metrics

5. **Verify Event Processing:**
   - Check if events queued in _event_loop
   - Monitor cooldown/paused state gates
   - Review trade_executor.py logs for failures
