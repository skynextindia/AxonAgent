# Installation & Setup Guide

Complete guide to install and run AxonAI with Velocity-Based Trailing Stop System.

---

## Prerequisites

### System Requirements
- **OS:** Windows 10/11
- **Python:** 3.9+
- **RAM:** 8GB minimum
- **Disk:** 2GB free space

### Required Software

1. **MetaTrader 5 (2 instances)**
   - Exness account (data feed terminal)
   - Default MT5 account (order execution terminal)
   
2. **Python 3.9+**
   - [Download from python.org](https://www.python.org/downloads/)
   - Add to PATH during installation

3. **Git**
   - [Download from git-scm.com](https://git-scm.com/download/win)

---

## Step 1: Clone Repository

```bash
# Clone the repo
git clone https://github.com/rohanaglawe/AxonAgent-Agy.git
cd AxonAgent-Agy

# Switch to velocity branch (or main for latest)
git checkout velocity

# Or create your own branch
git checkout -b your-branch-name
```

---

## Step 2: Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate

# On Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Key Dependencies
- `MetaTrader5` - MT5 connection
- `pandas` - Data manipulation
- `numpy` - Numerical computing
- `aiohttp` - Async HTTP for API
- `uvicorn` - ASGI server for dashboard
- `fastapi` - API framework

---

## Step 3: Configure MetaTrader 5 Terminals

### Terminal 1: Data Feed (Exness)
1. Open Exness MT5 terminal
2. File → Open an account
3. Select "Existing account"
4. Exness Live account credentials
5. Keep this terminal RUNNING (for data feed)

### Terminal 2: Order Execution (Default MT5)
1. Open default MT5 terminal (`C:\Program Files\MetaTrader 5\`)
2. File → Open an account
3. Your trading broker (e.g., Exness, IC Markets)
4. Keep this terminal RUNNING (for order execution)

### Verify Both Are Running
```bash
# Check running MT5 processes
tasklist | findstr terminal64
```

You should see TWO terminal64.exe processes.

---

## Step 4: Configure AxonAI

Edit `axonai/default_config.py`:

```python
# MT5 Terminal Paths
"mt5_terminal_path": "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",  # Data feed
"mt5_trade_terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",   # Execution

# Real-Time Settings
"realtime_enabled": True,           # Enable live trading
"realtime_dry_run": False,          # True=simulation, False=live orders
"paper_trade": False,               # False=real orders, True=simulated fills

# Magic Numbers (for order identification)
"realtime_magic_number": 123456,    # Base magic number
"realtime_default_lot_size": 0.01,  # Trade size (0.01 = 10K units for EURUSD)

# Entry Conditions
"realtime_min_signal_quality": 0.60,           # Min quality floor (0.55-0.70)
"realtime_min_confluence_conditions": 1,       # Min confluence rules to trigger

# Velocity Trailing
# (Edit axonai/realtime/velocity_trailing.py for thresholds)
```

**Key Variables:**
```python
velocity_acceleration_threshold = 1.2  # 20% faster = trail trigger
retest_window_pips = 3.0              # Price within 3 pips of SL
min_price_distance_to_trail = 2.0     # Minimum 2 pips from SL to trail
max_trail_distance = 15.0             # Maximum trail distance
```

---

## Step 5: Start the System

### Option A: Run Live Daemon

```bash
# Start real-time daemon for EURUSD
python -m cli.main live -t EURUSD=X

# The daemon will:
# 1. Connect to both MT5 terminals
# 2. Start monitoring price ticks
# 3. Detect entry signals (Rule A+B)
# 4. Execute auto-entry trades
# 5. Manage SL with velocity trailing
# 6. Launch dashboard on http://127.0.0.1:8000
```

### Option B: Manual Test Entry

```bash
# In another terminal (keep daemon running):
python test_live_entry.py

# This:
# 1. Opens BUY order on default MT5
# 2. Sets SL and TP
# 3. Daemon takes over trailing management
```

### Option C: Run Demo (No Real Trades)

```bash
# Simulate velocity trailing logic (no orders)
python demo_velocity_trailing.py

# Shows:
# - How velocity trailing calculates aggressiveness
# - Example SL trail scenarios
# - Without touching real accounts
```

---

## Step 6: Monitor Dashboard

**Open in browser:**
```
http://127.0.0.1:8000
```

### Dashboard Panels

**TIER 1: Entry Conditions**
- RULE A (MAX VEL): Velocity percentile
- RULE B (DIV): Displacement ratio
- TICK EFFICIENCY: Movement quality
- ENGINE TRIGGERING: State machine state

**TIER 2: Trade State (When Trade Open)**
- PHASE: ENTRY → EXPANSION → ... → EXIT
- HEALTH%: Thesis confidence
- MFE/MAE: Max favorable/adverse excursion
- PROFIT: Current P&L

**TIER 3: Account (From Execution Terminal)**
- BALANCE: Account balance
- EQUITY: Current equity
- PROFIT: Floating P&L
- POSITIONS: Open trades table

---

## Step 7: Check Logs

Monitor daemon behavior:

```bash
# Watch for entry signals
grep "state.*TRIGGERED" daemon_run.out

# Watch for trail updates
grep "VelocityTrail" daemon_run.out

# Watch for position management
grep "TRADE CLOSED" daemon_run.out

# Real-time follow
tail -f daemon_run.out | grep -E "TRIGGERED|VelocityTrail|CLOSED"
```

---

## Running Different Symbols

### EURUSD
```bash
python -m cli.main live -t EURUSD=X
```

### GBPUSD
```bash
python -m cli.main live -t GBPUSD=X
```

### Gold (XAUUSD)
```bash
python -m cli.main live -t XAUUSD=X
```

### Adjust for Each Symbol

Edit `axonai/realtime/velocity_trailing.py`:

```python
# For tight spreads (EURUSD, GBPUSD)
min_price_distance_to_trail = 1.5  # Trail tighter

# For wide spreads (Gold, Energy)
min_price_distance_to_trail = 5.0  # Trail wider

# For volatile assets
velocity_acceleration_threshold = 1.15  # More sensitive
```

---

## Troubleshooting

### "MT5 not connected"
```bash
# Verify both terminals are running
tasklist | findstr terminal64

# Verify paths in config match your installation
# Expected:
# C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe
# C:\Program Files\MetaTrader 5\terminal64.exe
```

### "Dashboard shows $0.00 balance"
```bash
# Confirm execution terminal is set correctly
# Check daemon logs:
grep "account" daemon_run.out

# Should show EXECUTION account, not Exness account
```

### "No entry signals triggering"
```bash
# Check velocity/displacement metrics
grep "ENGINE TRIGGERING" daemon_run.out

# Should cycle through: IDLE → ANOMALY → ARMING → TRIGGERED

# If stuck on ANOMALY:
# - Velocity not high enough (need 70th+ percentile)
# - Displacement not confirming (need IMPULSE classification)

# Adjust:
velocity_anomaly_threshold = 60  # Lower the bar
```

### "Trailing not triggering"
```bash
# Check velocity acceleration
grep "accel=" daemon_run.out

# Should show values like "accel=1.25" (25% faster)
# If all "accel=1.0", velocity is not changing

# Check health score
grep "health" daemon_run.out

# Must be > 50.0 to trail
```

### "Trades executing on wrong terminal"
```bash
# Verify in MT5 - check which account has the trade
# Should be on "default" MT5, NOT on Exness

# Logs should show:
# "Order to execution terminal" or ticket appears in default MT5
```

---

## Configuration Profiles

### Conservative (Low Risk)
```python
"realtime_min_signal_quality": 0.70         # Higher bar
"velocity_acceleration_threshold": 1.4      # Need 40% acceleration
"retest_window_pips": 2.0                   # Tighter retest window
"realtime_default_lot_size": 0.005          # Smaller lot size
```

### Aggressive (High Frequency)
```python
"realtime_min_signal_quality": 0.55         # Lower bar
"velocity_acceleration_threshold": 1.1      # Need 10% acceleration
"retest_window_pips": 4.0                   # Wider retest window
"realtime_default_lot_size": 0.02           # Larger lot size
```

### Balanced (Default)
```python
"realtime_min_signal_quality": 0.60         # Medium bar
"velocity_acceleration_threshold": 1.2      # Need 20% acceleration
"retest_window_pips": 3.0                   # Normal retest window
"realtime_default_lot_size": 0.01           # Standard lot size
```

---

## Testing Without Real Money

```bash
# Run in DRY_RUN mode first
# Edit config:
"realtime_dry_run": True,   # Simulation mode
"paper_trade": True,         # Simulated fills

# Then test
python -m cli.main live -t EURUSD=X

# Signals will print to logs but NO real orders sent
# Once you're confident, switch to realtime_dry_run=False
```

---

## File Structure

```
AxonAgent-Agy/
├── axonai/
│   ├── realtime/
│   │   ├── daemon.py                    # Main live engine
│   │   ├── velocity_trailing.py         # Trailing stop manager
│   │   ├── entry_state_machine.py       # Entry signal logic (FROZEN)
│   │   └── ...
│   ├── dataflows/
│   │   ├── mt5_data.py                  # MT5 connection
│   │   └── mt5_order_bridge.py          # Order routing
│   └── default_config.py                # Configuration
├── cli/
│   ├── main.py                          # Entry point
│   └── static/
│       └── index.html                   # Dashboard UI
├── test_live_entry.py                   # Manual test
├── demo_velocity_trailing.py            # Demo (no real trades)
├── VELOCITY_TRAILING.md                 # System documentation
├── INSTALLATION_GUIDE.md                # This file
└── requirements.txt                     # Dependencies
```

---

## Next Steps

1. **Verify both MT5 terminals are running**
2. **Configure paths in default_config.py**
3. **Run demo to understand velocity trailing:**
   ```bash
   python demo_velocity_trailing.py
   ```
4. **Run in dry-run mode to test signals:**
   ```bash
   # Set realtime_dry_run=True first
   python -m cli.main live -t EURUSD=X
   ```
5. **Monitor dashboard and logs**
6. **Switch to live mode when ready:**
   ```bash
   # Set realtime_dry_run=False
   python -m cli.main live -t EURUSD=X
   ```

---

## Support & Documentation

- **Velocity Trailing:** See `VELOCITY_TRAILING.md`
- **Entry Logic:** See `ENTRY_LOGIC.md` (frozen, not editable)
- **Dashboard:** Open `http://127.0.0.1:8000` while daemon runs
- **Logs:** Check `daemon_run.out` for detailed activity

---

## Advanced: Custom Symbols

To add a new symbol:

1. **Edit default_config.py:**
   ```python
   "mt5_symbol_suffix": ""  # For EURUSD
   # or
   "mt5_symbol_suffix": "m" # For some brokers
   ```

2. **Add to Market Watch in MT5:**
   - Right-click symbols panel
   - Add symbol name (e.g., EURUSD)

3. **Run:**
   ```bash
   python -m cli.main live -t EURUSD=X
   ```

---

**Ready to trade! Monitor the daemon logs and dashboard as the system executes trades with velocity-based trailing stops.**
