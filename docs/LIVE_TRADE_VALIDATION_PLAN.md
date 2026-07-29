# Live Trade Validation Plan
## 50+ Trade Verification of Velocity Spike Detection + Smart Cooldown

**Purpose:** Verify that the velocity spike detection system works in live market conditions and that the smart cooldown logic actually prevents whipsaws after false signals.

**Success criteria:** 
- ✅ Execute 50+ trades on live market (paper trading mode recommended)
- ✅ Capture metrics: false signal rate, recovery rate, cooldown effectiveness
- ✅ Verify each phase of the cooldown logic works in real conditions
- ✅ Decision point: proceed to multi-pair scaling or iterate back for improvements

---

## Setup Phase (30 minutes)

### Step 1: Configure Paper Trading Mode
```bash
# Edit axonai/default_config.py
paper_trade: True  # Simulates fills without sending real MT5 orders
realtime_dry_run: True  # Use demo account (not live money)
realtime_dynamic_sizing: False  # Fixed 0.01 lot (low risk during validation)
```

**Why paper trading?**
- Simulates real order fills (bid/ask, slippage, requotes)
- No real money at risk
- Fast iteration if we find issues
- Can replay same market conditions multiple times

### Step 2: Enable Trade Logging
```bash
# logs will be written to ~/.axonai/logs/axon.log (rotating file handler)
# Configure realtime/daemon.py for detailed logging:
logging.basicConfig(level=logging.DEBUG)  # Capture every entry decision
```

### Step 3: Create Validation Metrics File
```python
# docs/validation_results.md (we'll update this after each 10-trade batch)
# Columns:
# - Trade #
# - Entry signal (VELOCITY_SPIKE? SWEEP? LEVEL_BREACH?)
# - Entry price
# - Entry reason (ANOMALY->ARMING->TRIGGERED)
# - Current profit/loss at entry
# - False signal? (Y/N)
# - Cooldown applied (60s / 120s / 300s?)
# - Next signal within cooldown? (Y/N)
# - Result (recovery? whipsaw protected? stuck waiting?)
```

---

## Execution Phase (3-7 days of live market hours)

### Step 4: Start Live Daemon
```bash
# Start the daemon on EURUSD (most liquid, tight spreads)
python run.py --live --ticker EURUSD

# You should see:
# [2026-06-26 14:30:00] TickEngine: Initialized | Mt5Account: Demo | Balance: $10,000.00
# [2026-06-26 14:30:05] EventDetector: Ready (9 detectors active)
# [2026-06-26 14:30:05] EntryStateMachine: Starting at IDLE
# [2026-06-26 14:30:05] Dashboard: Running on http://0.0.0.0:8000
# [2026-06-26 14:30:05] AxonDaemon: Listening for events on queue
```

### Step 5: Monitor Dashboard in Real-Time
Open browser: `http://localhost:8000`

Watch for:
- **Regime display:** Is it TRENDING / RANGING / RANGE_CHOP?
- **Velocity percentile:** Current spike level (goal: >36th percentile to trigger ANOMALY)
- **Entry signals:** When TRIGGERED appears in the Agents tab
- **Cooldown countdown:** After each trade, see how many seconds until next entry allowed

### Step 6: Manual Trade Recording

**For each trade executed, record:**

```markdown
## Trade #1
- **Time:** 2026-06-26 14:32:15 UTC
- **Signal type:** VELOCITY_SPIKE (percentile: 68th)
- **Entry reason:** IDLE → ANOMALY (spike detected) → ARMING (absorption forming) → TRIGGERED (impulse breakaway)
- **Entry price:** 1.07832
- **Entry direction:** BUY
- **Current profit:** -0.5 pips (if this was after a previous loss)
- **Cooldown duration:** 60 seconds (no active position at entry)
- **Outcome:** +8 pips profit → Close after 120 seconds
- **False signal?** NO (price went up as predicted)
- **Notes:** Clean entry, no whipsaw, profit-taking exit

## Trade #2
- **Time:** 2026-06-26 14:34:10 UTC
- **Signal type:** SWEEP_DETECTED (liquidity hunt)
- **Entry direction:** SELL (expects reversal down)
- **Entry price:** 1.07845
- **Current profit:** +8 pips (from Trade #1)
- **Cooldown duration:** 300 seconds (winning trade active from #1... wait, #1 closed. So 60 sec?)
- **Outcome:** -12 pips loss → Market reversed UP (false signal)
- **False signal?** YES
- **Next signal at T+40 seconds:** BUY (market indeed going up)
- **Cooldown status:** 60 seconds allowed for losing trade → next signal allowed ✅
- **Recovery trade:** Entered BUY at 1.07862, +6 pips
- **Key insight:** Smart cooldown correctly allowed recovery; old 300s rule would have blocked this
```

---

## Measurement Phase (continuous during execution)

### Metrics to Track

**A) False Signal Rate**
```
Formula: False signals / Total signals × 100%
Example: 12 false signals out of 50 = 24% false signal rate

Goal: < 30% (velocity spike detection is probabilistic, not deterministic)
```

**B) Recovery Rate (after false signals)**
```
Formula: Successful recoveries / Total false signals × 100%
Example: 10 out of 12 false signals recovered = 83% recovery rate

Goal: > 75% (smart cooldown allows timely recovery entries)
```

**C) Cooldown Effectiveness**
```
Track each scenario:

1. No active position → 60s cooldown applied?
   ✓ Count how many times new entry happened within 60s
   ✓ Verify no entries blocked unnecessarily

2. Winning trade (+2+ pips) → 300s cooldown applied?
   ✓ Count protection wins (signal came within 300s but was safe to skip)
   ✓ Measure: whipsaw prevention value

3. Losing trade (-3+ pips) → 60s cooldown applied?
   ✓ Count recovery signals that arrived within 60s
   ✓ Verify recovery trades succeeded

4. Neutral position (-3 to +2 pips) → 120s cooldown applied?
   ✓ Count balanced decisions (not too eager, not too cautious)
```

**D) Win Rate & Profit Factor**
```
Traditional metrics (context only, not the focus):
- Win rate: Profitable trades / Total trades × 100%
- Profit factor: Gross profit / Gross loss ratio
- Average win: Total pips won / Number of wins
- Average loss: Total pips lost / Number of losses

For this validation, focus on:
- Pips gained from GOOD entries (velocity spike correctly identified)
- Pips lost from FALSE signals (velocity spike was noise)
- Pips gained from RECOVERY trades (smart cooldown allowed re-entry)
```

**E) State Machine Verification**
```
For every trade, verify the state progression:

Entry #1: IDLE → ANOMALY → ARMING → TRIGGERED ✓
Entry #2: IDLE → ANOMALY → ? (check if ARMING was reached)
Entry #3: IDLE → (no ANOMALY triggered) → still IDLE ✓

Count: How many entries hit TRIGGERED vs got stuck in ANOMALY?
Goal: > 80% of signals that triggered ANOMALY also reach TRIGGERED within 30 seconds
```

---

## Data Collection (Automated Logging)

### What the System Logs Automatically

**File: `~/.axonai/logs/axon.log` (rotating daily)**
```
[2026-06-26 14:32:15,234] INFO: TickEngine: Tick EURUSD 1.07832/1.07833 vol=120000
[2026-06-26 14:32:15,245] DEBUG: EventDetector: VOLATILITY_SPIKE detected (candle range 45 pips, ATR 18)
[2026-06-26 14:32:15,250] INFO: EntryStateMachine: IDLE → ANOMALY (velocity percentile: 68th)
[2026-06-26 14:32:20,130] INFO: EntryStateMachine: ANOMALY → ARMING (displacement absorption detected)
[2026-06-26 14:32:23,456] INFO: EntryStateMachine: ARMING → TRIGGERED (impulse breakaway confirmed)
[2026-06-26 14:32:24,100] INFO: TradeExecutor: Sending BUY order | Price: 1.07832 | SL: 1.07810 | TP: 1.07854
[2026-06-26 14:32:24,523] INFO: TradeExecutor: Order executed | Ticket: 123456 | Lot: 0.01 | Entry: 1.07832
```

**File: `reports/signals.jsonl` (JSON lines, one per trade)**
```json
{"timestamp":"2026-06-26T14:32:24Z","signal":"BUY","price":1.07832,"confidence":0.82,"velocity_percentile":68,"displacement":"IMPULSE","regime":"TRENDING"}
```

**File: `reports/trades.jsonl` (one line per closed trade)**
```json
{"ticket":123456,"direction":"BUY","entry_price":1.07832,"exit_price":1.07854,"profit_pips":2.2,"duration_seconds":120,"reason":"TP_HIT"}
```

### Parse These Logs After Trading

```python
# Quick analysis script
import json
from pathlib import Path

# Read trades from signals.jsonl
trades = []
with open("reports/signals.jsonl") as f:
    for line in f:
        trades.append(json.loads(line))

# Calculate metrics
total_trades = len(trades)
winning_trades = sum(1 for t in trades if t.get("profit_pips", 0) > 0)
win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

print(f"Total trades: {total_trades}")
print(f"Win rate: {win_rate:.1f}%")
print(f"Avg profit: {sum(t.get('profit_pips', 0) for t in trades) / total_trades if total_trades > 0 else 0:.1f} pips")
```

---

## Timeline & Checkpoint Plan

### Week 1: Initial 20 Trades
**Goal:** Verify entry state machine works in live conditions

**Run time:** 2-3 days of live market hours (London/New York sessions)

**Checkpoint questions:**
- ✅ Did the system start cleanly? (No TickEngine crashes?)
- ✅ Did entries trigger correctly? (IDLE → ANOMALY → TRIGGERED?)
- ✅ Were any trades blocked by spread? (realtime_max_spread_frac check)
- ✅ Did cooldown prevent double entries? (Same signal twice within 60s?)

**If issues found:** Pause, debug, iterate. Don't proceed to 50 trades.

**If clean:** Continue to Week 2.

### Week 2: Next 20 Trades
**Goal:** Verify smart cooldown prevents whipsaws + allows recovery

**Specifically track:**
- False signal rate: How many SWEEP signals were wrong? (expected: 20-30%)
- Recovery rate: Of those false signals, how many recovered? (expected: > 75%)
- Cooldown effectiveness: Did 60s cooldown on losses actually let recovery trades in?

**Checkpoint questions:**
- ✅ Was there a trade that lost money, then recovered within 60 seconds? (YES = good)
- ✅ Was there a trade that won, and the next signal was blocked for ~300s? (protection working?)
- ✅ No "stuck in ANOMALY" cases? (state machine always reached TRIGGERED or INVALIDATED?)

**If good:** Continue to Week 3.

**If issues:** Adjust thresholds (e.g., lower 36th percentile for more triggers, or raise it for fewer false signals).

### Week 3: Final 10+ Trades
**Goal:** Reach 50+ total, finalize statistics

**Checkpoint:**
- ✅ False signal rate converged? (stable around 20-30%?)
- ✅ Recovery rate stable? (> 75%?)
- ✅ Win rate reasonable? (40-50% is good for volatile entries)
- ✅ No crashes, no stuck positions, clean exits?

**Final decision:**
- **Green light:** Move to Phase 5.1 (multi-pair scaling)
- **Needs iteration:** Go back to Phase 3.1 (adjust cooldown thresholds)

---

## Sample Results Template

After completing 50+ trades, fill this out:

```markdown
# Live Validation Results — 50+ Trade Run
**Date:** 2026-06-26 to 2026-07-03  
**Duration:** 5 trading days (London/New York overlap)  
**Symbol:** EURUSD  
**Total trades:** 58  

## Key Metrics

| Metric | Result | Goal | Status |
|---|---|---|---|
| False signal rate | 24% | < 30% | ✅ PASS |
| Recovery rate (of false signals) | 83% | > 75% | ✅ PASS |
| Wins within 60s | 18/20 | > 80% | ✅ PASS |
| Profit protection (300s cooldown) | 8/10 | > 70% | ✅ PASS |
| State machine TRIGGERED rate | 94% | > 90% | ✅ PASS |
| Avg pips/trade (winners) | 4.2 | > 2.0 | ✅ PASS |
| Avg pips/trade (losers) | -3.1 | < -2.0 | ✅ PASS |
| Win rate | 47% | 40-50% | ✅ PASS |

## Cooldown Scenario Breakdown

### Scenario 1: No Active Position (60s cooldown)
- Times triggered: 22
- New signals within 60s: 18 (82%)
- Average time to next signal: 35 seconds
- **Insight:** Fast recovery working; entries not unnecessarily blocked

### Scenario 2: Winning Trade (+2+ pips, 300s cooldown)
- Times triggered: 12
- Signals within 300s cooldown: 11
- Whipsaws prevented: 8 (73%)
- **Insight:** Profit protection catching reversal signals that would hurt

### Scenario 3: Losing Trade (-3+ pips, 60s cooldown)
- Times triggered: 18
- Recovery signals within 60s: 15 (83%)
- Recovery trades profitable: 12 (80%)
- **Insight:** Smart cooldown correctly allowing re-entry; 80% of recovery trades worked

### Scenario 4: Neutral Position (-3 to +2 pips, 120s cooldown)
- Times triggered: 6
- Balanced outcome: Mixed (3 good, 2 stopped by cooldown, 1 filled)
- **Insight:** 120s neutral cooldown may be too aggressive; consider 90s?

## Improvement Recommendations

1. **Cooldown timing:** Change neutral from 120s to 90s (finding: too many stuck trades)
2. **Velocity threshold:** Current 36th percentile good (low false signal rate)
3. **Recovery trade sizing:** Consider 1.5× lot on recovery entries (higher success rate)
4. **Next phase:** Ready for multi-pair scaling (no issues found in EURUSD)

## Decision

✅ **APPROVED FOR MULTI-PAIR SCALING** (Phase 5.1)

Proceed to implement concurrent trading on GBPUSD, USDJPY, AUDUSD with shared regime detection.
```

---

## Tools You'll Need

### 1. Live Market Monitoring
- MT5 terminal open (real quotes, even if paper trading)
- Browser tab: `http://localhost:8000` (dashboard)
- Text editor: Track trades manually or script automated capture

### 2. Log Parsing
```bash
# Check daemon health every 10 trades
tail -50 ~/.axonai/logs/axon.log | grep -E "EntryStateMachine|Cooldown|Order executed"

# Count trades
grep "Order executed" ~/.axonai/logs/axon.log | wc -l

# Extract all TRIGGERED signals
grep "TRIGGERED" ~/.axonai/logs/axon.log > validation_triggers.log
```

### 3. Quick Metrics Script
Create `scripts/validate_trades.py`:
```python
#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict

# Read all trades
trades = []
with open("reports/trades.jsonl") as f:
    for line in f:
        trades.append(json.loads(line))

# Group by outcome
outcomes = defaultdict(list)
for trade in trades:
    reason = trade.get("reason", "UNKNOWN")
    outcomes[reason].append(trade)

print(f"\n{'='*60}")
print(f"TRADE VALIDATION REPORT")
print(f"{'='*60}")
print(f"\nTotal trades: {len(trades)}")

# Win rate
wins = len([t for t in trades if t.get("profit_pips", 0) > 0])
print(f"Win rate: {wins}/{len(trades)} = {wins/len(trades)*100:.1f}%")

# Avg pips
avg_pips = sum(t.get("profit_pips", 0) for t in trades) / len(trades) if trades else 0
print(f"Avg pips/trade: {avg_pips:.1f}")

# Grouped by outcome
print(f"\nBy outcome:")
for reason, trades_subset in outcomes.items():
    count = len(trades_subset)
    avg = sum(t.get("profit_pips", 0) for t in trades_subset) / count if trades_subset else 0
    print(f"  {reason}: {count} trades, avg {avg:.1f} pips")

print(f"{'='*60}\n")
```

Run after each 10-trade batch:
```bash
python scripts/validate_trades.py
```

---

## Success Criteria (Exit Conditions)

### ✅ Proceed to Phase 5.1 (Multi-Pair) if:
- [x] 50+ trades completed without system crashes
- [x] False signal rate 20-30% (expected range)
- [x] Recovery rate > 75% (cooldown allowing re-entry)
- [x] Win rate > 40% (not losing money systematically)
- [x] No stuck positions (all trades closed normally)
- [x] State machine reaching TRIGGERED > 90% of the time

### ⚠️ Return to Phase 3.1 (Iterate) if:
- [ ] False signal rate > 40% (too much noise in detection)
- [ ] Recovery rate < 60% (cooldown not helping recovery)
- [ ] Crashes or hung processes during run
- [ ] Win rate < 30% (consistently losing)
- [ ] Cooldown times seem wrong (adjusted thresholds needed)

### 🛑 Hard Stop if:
- [ ] System loses > 5% of account equity (risk guard failure)
- [ ] Same position stuck open for > 2 hours (exit logic broken)
- [ ] Dashboard becomes unresponsive (API server crash)

---

## Expected Timeline

| Phase | Duration | Expected Outcome |
|---|---|---|
| Setup | 30 minutes | Config ready, logging enabled |
| Week 1 (20 trades) | 2-3 days | Verify entry state machine |
| Week 2 (20 trades) | 2-3 days | Verify cooldown effectiveness |
| Week 3 (10+ trades) | 1-2 days | Reach 50+ total, finalize stats |
| Analysis | 1-2 hours | Fill out results template |
| **Total** | **5-8 days** | **Decision: Scale or iterate** |

---

## Failure Recovery

**If the daemon crashes:**
```bash
# Check logs
tail -100 ~/.axonai/logs/axon.log

# Search for error
grep -i "error\|exception\|traceback" ~/.axonai/logs/axon.log | tail -20

# Restart
python run.py --live --ticker EURUSD
```

**If trades get stuck (not closing):**
```python
# Manual close via dashboard
# POST to http://localhost:8000/api/close_all
# Or manually close in MT5 terminal
```

**If cooldown seems wrong (trades blocked incorrectly):**
- Check current_profit_pips in logs
- Verify trade_state_engine.py is reading correct profit
- If threshold issue, adjust in daemon.py lines 1287-1309

---

## Next Steps After Validation

**If validation succeeds (50+ trades, metrics green):**
1. Write summary report to `docs/VALIDATION_RESULTS.md`
2. Create GitHub PR: "Phase 5.1: Multi-pair orchestration"
3. Scale to GBPUSD, USDJPY, AUDUSD
4. Implement portfolio-level position sizing

**If iteration needed:**
1. Identify which cooldown threshold was wrong
2. Adjust in `daemon.py:_seconds_until_ready()`
3. Restart with new values
4. Re-run 20-trade validation batch
5. Repeat until metrics converge

---

## Summary

This is a **5-8 day validation plan** to run 50+ live trades and measure:
- ✅ False signal rate (expect 20-30%)
- ✅ Recovery rate (expect > 75%)
- ✅ Cooldown effectiveness (each scenario tested)
- ✅ Win rate (expect 40-50%)
- ✅ State machine reliability (> 90% TRIGGERED)

**Green light:** Proceed to multi-pair orchestration.  
**Red light:** Iterate back to Phase 3.1 with adjusted thresholds.

Good luck! 🚀
