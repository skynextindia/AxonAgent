# Deployment Guide: Running AxonAI on Another Machine

Complete guide to deploy AxonAI velocity trading system on a new machine.

---

## Phase 1: Machine Setup

### 1.1 System Requirements
- **OS:** Windows 10/11
- **Python:** 3.9+ (3.11+ recommended)
- **RAM:** 8GB minimum (16GB recommended)
- **Disk:** 5GB free space
- **Network:** Internet connection (for MT5 data feed)
- **Administrator rights:** Required for Python installation

### 1.2 Install Python 3.11+

1. Download from [python.org](https://www.python.org/downloads/)
2. **IMPORTANT:** Check "Add Python to PATH" during installation
3. Verify installation:
   ```bash
   python --version
   ```

### 1.3 Install Git

Download from [git-scm.com](https://git-scm.com/download/win)

### 1.4 Install MetaTrader 5 (Both Terminals)

You need **TWO separate MT5 installations**:

#### Terminal 1: Data Feed (Exness)
```
Path: C:\Program Files\MetaTrader 5 EXNESS\
Login: Exness live account
Purpose: Price tick data only
```

#### Terminal 2: Order Execution (Default MT5)
```
Path: C:\Program Files\MetaTrader 5\
Login: Your broker account
Purpose: Order execution + trade management
```

**Both terminals must be logged in and running while daemon operates.**

---

## Phase 2: Clone Repository

```bash
# Choose a working directory
cd C:\Trading

# Clone repository
git clone https://github.com/rohanaglawe/AxonAgent-Agy.git
cd AxonAgent-Agy

# Switch to velocity branch (latest stable)
git checkout velocity
```

---

## Phase 3: Python Environment Setup

### 3.1 Create Virtual Environment

```bash
# Create venv
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
source venv/bin/activate
```

### 3.2 Install All Dependencies

```bash
# Install from pyproject.toml
pip install -e .

# Verify key dependencies installed
pip list | findstr pandas
pip list | findstr MetaTrader5
```

Expected output:
```
pandas                    2.3.X
MetaTrader5              5.0.X
fastapi                  0.1X0.X
uvicorn                  0.28.X
websockets              12.0
```

---

## Phase 4: Configure AxonAI

### 4.1 Edit Configuration File

Open: `axonai/default_config.py`

**CRITICAL SETTINGS:**

```python
# Terminal Paths (Verify these match YOUR installation)
"mt5_terminal_path": "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",
"mt5_trade_terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",

# Real-Time Settings (Enable live trading)
"realtime_enabled": True,
"realtime_dry_run": False,      # Set to True for simulation first
"paper_trade": False,            # False = real orders

# Broker Configuration
"mt5_symbol_suffix": "",         # "" for Exness, "m" for some others
"realtime_default_lot_size": 0.01,  # Adjust based on account size

# Entry Thresholds
"realtime_min_signal_quality": 0.60,  # Signal quality floor (0.55-0.70)
```

### 4.2 Customize for Your Broker

If using a different broker:

```python
# For IC Markets
"mt5_symbol_suffix": "m"

# For eToro
"mt5_symbol_suffix": ".et"

# Check with broker what suffix is needed
```

### 4.3 Velocity Trailing Configuration

Edit: `axonai/realtime/velocity_trailing.py`

```python
# For EURUSD (tight spread)
velocity_acceleration_threshold = 1.2   # 20% faster
retest_window_pips = 3.0               # 3 pips from SL
min_price_distance_to_trail = 2.0      # Trail if 2+ pips away

# For Gold (wide spread)
velocity_acceleration_threshold = 1.15
retest_window_pips = 5.0
min_price_distance_to_trail = 5.0
```

---

## Phase 5: Verify MT5 Setup

### 5.1 Check Terminal Paths

```bash
# Verify Exness terminal exists
ls "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"

# Verify default MT5 terminal exists
ls "C:\Program Files\MetaTrader 5\terminal64.exe"
```

### 5.2 Start Both Terminals

**Before running daemon:**
1. Open Exness MT5 → Log in
2. Open default MT5 → Log in
3. Add EURUSD to Market Watch in BOTH terminals
4. Keep both running in background

### 5.3 Verify Connectivity

```bash
# Test if Python can connect to MT5
python -c "import MetaTrader5 as mt5; print('MT5 module loaded')"
```

---

## Phase 6: Initial Test (Dry Run)

### 6.1 Enable Dry-Run Mode

Edit `axonai/default_config.py`:
```python
"realtime_dry_run": True,    # ENABLE for first run
"paper_trade": True,          # Simulated fills
```

### 6.2 Start Daemon

```bash
# Ensure venv is activated
venv\Scripts\activate

# Start daemon
python -m cli.main live -t EURUSD=X
```

**Expected output:**
```
Starting Web GUI Dashboard on http://127.0.0.1:8000/
AxonDaemon starting for EURUSD=X (MT5: EURUSD)
Step 1/4: MT5 data feed connected
Step 1B/4: MT5 order bridge started (dual-terminal mode via subprocess)
Step 2/4: Cold-starting live state...
...
AxonDaemon LIVE. Monitoring EURUSD=X in real-time.
```

### 6.3 Monitor Dashboard

Open browser: `http://127.0.0.1:8000`

Check:
- ✅ Dashboard loads
- ✅ Account balance displays (not $0.00)
- ✅ Price ticks flowing
- ✅ Regime indicators updating

### 6.4 Check Logs

```bash
# In another terminal
tail -f ~/.axonai/logs/axon.log

# Watch for entry signals (even in dry-run)
grep "ANOMALY\|TRIGGERED\|Broadcasting" ~/.axonai/logs/axon.log
```

Run for **30 minutes** in dry-run mode. Verify:
- Price ticks arriving
- Velocity metrics updating
- Displacement classification working
- Entry state machine transitioning (if market conditions trigger)

---

## Phase 7: Switch to Live Trading

**Only after confirming dry-run works!**

### 7.1 Disable Dry-Run

Edit `axonai/default_config.py`:
```python
"realtime_dry_run": False,     # DISABLE for live
"paper_trade": False,          # Real orders
```

### 7.2 Reduce Position Size (Optional)

Start smaller for confidence:
```python
"realtime_default_lot_size": 0.005,  # Instead of 0.01
```

### 7.3 Start Live Daemon

```bash
# Kill existing daemon (Ctrl+C in previous terminal)

# Restart with live settings
python -m cli.main live -t EURUSD=X
```

### 7.4 Monitor First Trade

- Watch dashboard for entry signals
- Verify order appears in MT5 execution terminal
- Check velocity trailing is managing SL
- Monitor trade P&L in dashboard

---

## Phase 8: Remote Deployment (Optional)

If deploying to a VPS/Cloud machine:

### 8.1 Network Setup

```bash
# Open firewall for dashboard (if remote)
# Add inbound rule: Port 8000 TCP

# Or use SSH tunnel from local machine
ssh -L 8000:localhost:8000 user@remote-ip
```

### 8.2 Run as Service (Windows)

Create batch file `start_daemon.bat`:
```batch
@echo off
cd C:\Trading\AxonAgent-Agy
call venv\Scripts\activate
python -m cli.main live -t EURUSD=X >> daemon.log 2>&1
```

Use Task Scheduler to run at startup.

### 8.3 Run as Service (Linux)

Create systemd service:
```ini
[Unit]
Description=AxonAI Daemon
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/AxonAgent-Agy
ExecStart=/home/trader/AxonAgent-Agy/venv/bin/python -m cli.main live -t EURUSD=X
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## Phase 9: Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'pandas'"

```bash
# Reinstall dependencies
pip install -e .

# Or specific packages
pip install pandas>=2.3.0 MetaTrader5>=5.0.45
```

### Issue: "MT5 not connected"

```bash
# Check both terminals are running
tasklist | findstr terminal64

# Verify paths in config match your installation
# Expected: Two terminal64.exe processes
```

### Issue: "Port 8000 already in use"

```bash
# Kill existing process
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### Issue: "Dashboard shows $0.00 balance"

- Hard refresh browser: `Ctrl+Shift+R`
- Check execution terminal is logged in
- Verify account has balance

### Issue: "No entry signals detecting"

- Check market volatility (needs velocity spike)
- Verify displacement classification (should see ANOMALY logs)
- Confirm tick flow is active (check tick count in STATS)

---

## Phase 10: Monitoring & Maintenance

### Daily Checks

```bash
# Monitor logs for errors
tail -50 ~/.axonai/logs/axon.log

# Check daemon uptime
grep "STATS:" ~/.axonai/logs/axon.log | tail -1

# Verify account data updating
grep "Broadcasting account" ~/.axonai/logs/axon.log | tail -5
```

### Weekly Tasks

- Review trade analytics: `~/.axonai/logs/trade_analytics.jsonl`
- Check velocity trailing effectiveness
- Monitor win rate and MFE/MAE metrics
- Verify SL adjustments are working correctly

### Account Maintenance

- Ensure both MT5 terminals stay logged in
- Monitor account balance for drawdowns
- Review cooldown periods if losses occur
- Adjust lot size based on account performance

---

## Configuration Presets

### Conservative (Low Risk)
```python
"realtime_default_lot_size": 0.005
"realtime_min_signal_quality": 0.70
velocity_acceleration_threshold = 1.4
retest_window_pips = 2.0
```

### Aggressive (High Frequency)
```python
"realtime_default_lot_size": 0.02
"realtime_min_signal_quality": 0.55
velocity_acceleration_threshold = 1.1
retest_window_pips = 4.0
```

### Balanced (Default)
```python
"realtime_default_lot_size": 0.01
"realtime_min_signal_quality": 0.60
velocity_acceleration_threshold = 1.2
retest_window_pips = 3.0
```

---

## Testing Different Symbols

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

### Crypto (BTCUSD via Exness)
```bash
python -m cli.main live -t BTCUSD=X
```

---

## Deployment Checklist

- [ ] Python 3.9+ installed
- [ ] Git installed
- [ ] Repository cloned to machine
- [ ] Virtual environment created and activated
- [ ] All dependencies installed (`pip install -e .`)
- [ ] Both MT5 terminals installed and running
- [ ] Configuration paths verified
- [ ] Dry-run mode tested (30 minutes minimum)
- [ ] Dashboard accessible on http://127.0.0.1:8000
- [ ] Account balance displaying correctly
- [ ] Entry signals detected in logs
- [ ] Live mode enabled
- [ ] First trade executed successfully
- [ ] Velocity trailing updating SL correctly

---

## Support & Documentation

- **System Overview:** See README.md
- **Velocity Trailing:** See VELOCITY_TRAILING.md
- **Entry Logic:** See ENTRY_LOGIC.md
- **Installation:** See INSTALLATION_GUIDE.md
- **Dashboard:** Open http://127.0.0.1:8000 while daemon runs
- **Logs:** Check ~/.axonai/logs/axon.log for diagnostics

---

**You're ready to deploy! Start with dry-run mode, verify everything works, then switch to live trading.**
