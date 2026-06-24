# Velocity Intelligence System - Live Trading Guide

## Quick Start

### 1. **Start the Daemon**

```bash
cd D:\AXON.AI\AxonAgent
python start_velocity_daemon.py --mode demo --symbol EURUSD
```

**Options:**
```
--mode demo         # Demo account (recommended for testing)
--mode live         # Live account (careful!)
--mode paper        # Paper trading (simulation)
--symbol EURUSD     # Trading pair
--lot-size 0.01     # Position size (default: 0.01)
--cooldown 300      # Entry cooldown in seconds (default: 300 = 5 min)
```

### 2. **Monitor in Dashboard**

Open browser:
```
http://127.0.0.1:8000/
```

Watch in real-time:
- ✅ Live ticks and candles
- 📊 Entry signals and qualification
- 🎯 Trade health scores
- 💹 Velocity metrics
- 🚪 Exit reasons and decisions

### 3. **Stop Daemon**

Press `Ctrl+C` in terminal - daemon shuts down gracefully

---

## What Happens When Daemon Starts

### Phase 1: Baseline Building (5-10 minutes)
- System samples velocity for every tick
- Builds baseline: mean, std, peak
- **No trades yet** - just data collection

### Phase 2: Live Trading (Continuous)
- Entry: Signal fires → Velocity z-score checked
  - If z-score < 2.0 → **REJECTED** (too weak)
  - If z-score ≥ 2.0 → **ACCEPTED** (strong impulse)
- Trade registered with health monitor
- Health updated every tick:
  - Track velocity behavior
  - Detect reversal factors
  - Calculate health score (0-1)
- Exit triggers:
  - Health < 0.40 → **CLOSE** (health died)
  - Reversal risk > 0.70 → **CLOSE** (reversal detected)
  - Health < 0.70 → **TIGHTEN TRAIL** (health degrading)

---

## Configuration Parameters

Edit `axonai/default_config.py` to tune:

```python
"realtime_entry_zscore_threshold": 2.0              # Lower = more entries
"realtime_velocity_health_threshold_exit": 0.40     # Lower = earlier exits
"realtime_velocity_health_threshold_trail": 0.70    # Higher = wider trails
"realtime_reversal_risk_threshold": 0.70            # Lower = more exits
"realtime_velocity_window_size": 30                 # Larger = smoother
"realtime_pre_entry_baseline_window": 100           # Larger = stable baseline
"realtime_velocity_min_profit_tight_trail": 0.25    # Min profit before trailing
```

---

## Real-Time Metrics to Monitor

### Entry Metrics
```
Entry Attempts:     How many signals fired
Entry Rejections:   How many failed z-score check
Rejection Rate:     Percentage of signals rejected
Avg Entry Z-Score:  Should be > 2.0 if accepted
```

### Trade Health Metrics
```
Health Score:       1.0 = perfect, 0.0 = dead
Velocity Trend:     ACCELERATING/STABLE/DECAYING/OSCILLATING
Velocity Decay:     Current / Peak velocity (0-1)
Reversal Risk:      0-1 scale, sum of 5 factors:
  - Velocity collapse (< 50% of peak)
  - Exhaustion phase
  - Regime shift
  - Back to baseline
  - MTF misalignment
```

### Exit Metrics
```
Exit Reason:        Why trade closed
- HOLD: Healthy, no action
- TIGHT_TRAIL: Health degrading, adapting trail
- CLOSE_ON_REVERSAL: Reversal factors detected
- CLOSE_ON_HEALTH: Health score collapsed
```

---

## Typical Daily Workflow

### Morning (7:00 AM UTC - London Open)
```bash
python start_velocity_daemon.py --mode demo --symbol EURUSD
# Baseline builds during pre-London chop
# First entries expected around 8:00-9:00 AM UTC
```

### During Day
- Monitor dashboard every 1-2 hours
- Check logs: `velocity_daemon.log`
- Verify health scores on open trades
- No intervention needed - system is autonomous

### Before Close (17:00 UTC - NY Open End)
- Check for open positions
- System has exit rules, but can manually close before gap risk
- Review day's metrics

### End of Day
- Press `Ctrl+C` to gracefully shutdown
- Review `velocity_daemon.log` for issues
- Check trade metrics in reports/

---

## Sample Dashboard Indicators

### Health Score Progression (Good Trade)
```
Entry:      health_score = 1.0   (velocity spike detected, all good)
+2 min:     health_score = 0.95  (stable, slight noise)
+5 min:     health_score = 0.90  (still good)
+10 min:    health_score = 0.75  (starting to fade) → trail tightens
+15 min:    health_score = 0.50  (momentum dying)
+20 min:    health_score = 0.30  (reversal risk rising)
Exit:       health_score = 0.20  (CLOSE triggered, trade closed)
```

### Health Score Progression (Bad Entry)
```
Entry:      health_score = 1.0   (z-score = 2.1, barely qualified)
+1 min:     health_score = 0.8   (displacement weak)
+3 min:     health_score = 0.5   (momentum already fading)
+5 min:     health_score = 0.35  (CLOSE triggered - health < 0.40)
```

---

## Troubleshooting

### Issue: "MT5 Connection Failed"
**Solution:**
- Open MetaTrader 5 terminal
- Ensure you're logged in
- Daemon needs running MT5 to connect
- Restart daemon after MT5 is ready

### Issue: "No Trades Firing"
**Possibilities:**
1. Baseline still building (first 5-10 min)
2. Market velocity weak (z-score < 2.0)
3. Outside London/NY session
4. Spread too wide

**Check:**
```
- Open dashboard
- Look at "Entry Z-Score"
- Watch "Rejection Rate"
- Verify "Session" shows London or NY
```

### Issue: "Too Many Entries"
**Solution:**
Increase entry threshold:
```python
"realtime_entry_zscore_threshold": 2.5  # Was 2.0
```

### Issue: "Exiting Too Early"
**Solution:**
Raise health exit threshold:
```python
"realtime_velocity_health_threshold_exit": 0.50  # Was 0.40
```

### Issue: "Exits Too Late"
**Solution:**
Lower health exit threshold:
```python
"realtime_velocity_health_threshold_exit": 0.30  # Was 0.40
```

---

## Safety Features

✅ **Circuit Breaker:** Stops trading if daily drawdown > 5%  
✅ **Position Limit:** Max 1 open position at a time  
✅ **Cooldown Gate:** 5 min cooldown between entries  
✅ **Spread Check:** Rejects entries if spread too wide  
✅ **Session Filter:** Only trades London 8am-12pm, NY 1pm-5pm UTC  

---

## What to Watch For

### Good Signs
- ✅ Entry rejection rate 20-40% (system being picky)
- ✅ Wins have health_score > 0.6 at exit
- ✅ Losses have health_score < 0.4 at exit
- ✅ Trail tightens as health degrades
- ✅ Exits happen before big reversals

### Bad Signs
- ❌ All entries accepted (threshold too low)
- ❌ Exits way too early (health threshold too low)
- ❌ Exits way too late (reversal risk ignored)
- ❌ Health score not changing during trade

---

## Logging & Reports

**Live Log:**
```bash
tail -f velocity_daemon.log
```

**Daily Report:**
- Trades: `reports/trades_YYYYMMDD.json`
- Health: `reports/health_YYYYMMDD.csv`
- Metrics: `reports/metrics_YYYYMMDD.json`

---

## Demo Account Setup (Recommended First)

1. Open MT5 terminal
2. File → Open Account
3. Select any demo broker
4. Login to demo account
5. Run daemon: `python start_velocity_daemon.py --mode demo`
6. Test for 1-2 days
7. Verify metrics are good
8. Then switch to live (if confident)

---

## Emergency Stop

If something goes wrong:
```bash
# Press Ctrl+C in daemon terminal
# Or kill process:
# taskkill /F /IM python.exe
```

Daemon saves state on exit, can resume cleanly.

---

**Ready to trade? Start the daemon and watch it execute!**

Questions? Check `velocity_daemon.log` for detailed execution trace.
