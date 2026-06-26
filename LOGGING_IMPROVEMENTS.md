# Trade Journal Logging Improvements

## Problem Solved
The trade journal was showing corrupted/static data with:
- ❌ UNKNOWN strategies instead of actual exit triggers
- ❌ Manual Close / Unknown instead of real exit reasons
- ❌ 0.0 P&L instead of actual profit/loss
- ❌ Missing velocity trailing decisions
- ❌ No exit strategy information

## Solution Implemented

### 1. Backend Enhancements (daemon.py)

#### New Tracking Dictionaries
```python
self._active_trade_exit_reasons: dict[int, dict] = {}
self._active_trade_velocity_events: dict[int, list] = {}
```

#### Exit Engine Integration
When ExitEngine closes a position, we now capture:
- `reason`: What triggered the exit (thesis_failure, adverse_impulse, exhaustion)
- `strategy`: Identifier (exit_engine)
- `urgency`: Urgency score (0.0-1.0)

```python
self._active_trade_exit_reasons[ticket] = {
    "reason": exit_signal.reason,
    "strategy": exit_signal.strategy,
    "urgency": exit_signal.urgency,
    "details": exit_signal.details
}
```

#### Velocity Trailing Events
When SL is adjusted by velocity trailing, we record:
- Time of adjustment
- Old SL → New SL prices
- Reason for adjustment

```python
self._active_trade_velocity_events[ticket].append({
    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "old_sl": pos_sl,
    "new_sl": new_sl,
    "reason": "velocity_trailing"
})
```

### 2. Enhanced Trade Logging

Trade closure now records complete lifecycle data:
```json
{
  "timestamp": "2026-06-26 15:23:45",
  "type": "trade_closed",
  "ticket": 12345,
  "exit_strategy": "exit_engine",
  "exit_urgency": 0.85,
  "velocity_trailing_events": [
    {
      "time": "2026-06-26 15:20:15",
      "old_sl": 1.13985,
      "new_sl": 1.13995,
      "reason": "velocity_trailing"
    }
  ],
  "reason": "thesis_failure",
  "profit": +15.50,
  "pips": +155.0,
  "outcome": "WIN"
}
```

### 3. Dashboard Display

#### Main Journal Table
- New "Exit Strategy" column showing the exit trigger (amber colored)
- Displays alongside Reason, Entry, Exit, P&L, Outcome

#### Expanded Trade Details
Shows three columns:
1. **Trade Details**
   - Ticket, System, Duration
   - Peak Reached, Max Drawdown
   - **Exit Strategy & Urgency** (new)

2. **Rule A/B Metrics**
   - Trigger Event, Priority
   - Velocity Divergence, Tick Efficiency, Peak Confidence

3. **Market Context**
   - Regime, Volatility, Spread, Session

#### Velocity Trailing Section
When trade has SL adjustments, shows a separate section:
```
⚡ Velocity Trailing Events
2026-06-26 15:20:15  1.13985 → 1.13995  velocity_trailing
2026-06-26 15:21:30  1.13995 → 1.14005  velocity_trailing
```

## What You'll Now See in Journal

Instead of:
```
EURUSD UNKNOWN -- 1.13485 -- 0.0 0.00 Manual Close / Unknown BREAKEVEN
```

You'll see:
```
EURUSD exit_engine -- 1.13485 -- 15.50 155.0 Thesis Failure exit_engine WIN
```

With expanded details showing:
- Exit Strategy: `exit_engine` (amber)
- Exit Urgency: `85%` (how urgent the close was)
- Exit Reason: `thesis_failure` (why it closed)
- Velocity Trailing Events: Complete SL adjustment history

## Files Changed

1. **daemon.py**
   - Added exit reason tracking
   - Added velocity event tracking
   - Enhanced trade_closed payload

2. **api_server.py**
   - Exposed exit_strategy and exit_urgency in trade response
   - Included velocity_trailing_events in response

3. **index.html**
   - Added Exit Strategy column to table
   - Enhanced expanded details with exit data
   - Added velocity trailing events display section

## Testing

Run paper trading to verify:
```bash
python run.py --direct --symbol EURUSD --paper
```

Then:
1. Open dashboard at http://127.0.0.1:8000
2. Navigate to "Journal" view
3. Closed trades should show exit strategy (not UNKNOWN)
4. Click trade to expand and see velocity trailing history
5. Exit urgency shown as percentage

## Benefits

✓ Full visibility into trade exit decisions
✓ See when and why SL was adjusted (velocity trailing)
✓ Understand exit engine urgency levels
✓ Complete trade lifecycle from entry to exit
✓ Distinguish between manual, SL, and engine-driven exits
