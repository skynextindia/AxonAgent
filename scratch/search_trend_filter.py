# File: scratch/search_trend_filter.py
import sys
import subprocess
import json
from pathlib import Path

# We will test thresholds from 0.0 to 0.8
thresholds = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

# We need to programmatically update entry_state_machine.py, run the backtest, and collect the JSON report.
# Let's write a helper to replace the threshold in axonai/realtime/entry_state_machine.py

esm_path = Path("axonai/realtime/entry_state_machine.py")
assert esm_path.exists(), "entry_state_machine.py not found!"

original_content = esm_path.read_text(encoding="utf-8")

def restore():
    esm_path.write_text(original_content, encoding="utf-8")

try:
    results = []
    for threshold in thresholds:
        print(f"\n================= TESTING THRESHOLD: {threshold} =================", flush=True)
        # Modify entry_state_machine.py
        # We will replace the strong trend block line:
        # (self._anomaly_direction == "BUY" and (mtf.h1_bias < -0.7 or mtf.h4_bias < -0.7)) or \
        # (self._anomaly_direction == "SELL" and (mtf.h1_bias > 0.7 or mtf.h4_bias > 0.7))
        # with:
        # (self._anomaly_direction == "BUY" and (mtf.h1_bias < -{threshold} or mtf.h4_bias < -{threshold})) or \
        # (self._anomaly_direction == "SELL" and (mtf.h1_bias > {threshold} or mtf.h4_bias > {threshold}))
        
        modified = original_content
        # First check: block BUY if both timeframes bearish, SELL if both bullish
        target_block = """            if (self._anomaly_direction == "BUY" and mtf.h4_bias < -0.8) or \\
               (self._anomaly_direction == "SELL" and mtf.h4_bias > 0.8):
                self._transition(STATE_INVALIDATED, "Blocked: Trading against strong H4 trend")"""
                
        # We will replace it with the new balance trend filter:
        if threshold == 0.0:
            # Pure OR filter
            replacement_block = """            is_trend_blocked = False
            if self._anomaly_direction == "BUY" and (mtf.h1_bias < 0.0 or mtf.h4_bias < 0.0):
                is_trend_blocked = True
            elif self._anomaly_direction == "SELL" and (mtf.h1_bias > 0.0 or mtf.h4_bias > 0.0):
                is_trend_blocked = True

            if is_trend_blocked:
                self._transition(STATE_INVALIDATED, f"Blocked: Trend filter (H1={mtf.h1_bias:.2f}, H4={mtf.h4_bias:.2f})")"""
        else:
            replacement_block = f"""            is_trend_blocked = False
            if self._anomaly_direction == "BUY" and mtf.h1_bias < 0.0 and mtf.h4_bias < 0.0:
                is_trend_blocked = True
            elif self._anomaly_direction == "SELL" and mtf.h1_bias > 0.0 and mtf.h4_bias > 0.0:
                is_trend_blocked = True

            if is_trend_blocked:
                self._transition(STATE_INVALIDATED, f"Blocked: Trend filter (H1={{mtf.h1_bias:.2f}}, H4={{mtf.h4_bias:.2f}})")
            elif (self._anomaly_direction == "BUY" and (mtf.h1_bias < -{threshold} or mtf.h4_bias < -{threshold})) or \\
                 (self._anomaly_direction == "SELL" and (mtf.h1_bias > {threshold} or mtf.h4_bias > {threshold})):
                self._transition(STATE_INVALIDATED, f"Blocked: Trading against strong trend (H1={{mtf.h1_bias:.2f}}, H4={{mtf.h4_bias:.2f}})")"""
                
        modified = modified.replace(target_block, replacement_block)
        esm_path.write_text(modified, encoding="utf-8")
        
        # Run May backtest
        print("Running May backtest...", flush=True)
        may_cmd = [sys.executable, "run_intraday_backtest.py", "--start", "2026-05-01", "--end", "2026-05-30"]
        subprocess.run(may_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        # Find latest JSON report for May
        may_reports = sorted(Path("reports").glob("intraday_bt_EURUSD_20260501_20260530_*.json"))
        with open(may_reports[-1], "r") as f:
            may_data = json.load(f)
            
        # Run June backtest
        print("Running June backtest...", flush=True)
        june_cmd = [sys.executable, "run_intraday_backtest.py", "--start", "2026-06-01", "--end", "2026-06-21"]
        subprocess.run(june_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        june_reports = sorted(Path("reports").glob("intraday_bt_EURUSD_20260601_20260621_*.json"))
        with open(june_reports[-1], "r") as f:
            june_data = json.load(f)
            
        res = {
            "threshold": threshold,
            "may_trades": may_data["total_trades"],
            "may_win_rate": may_data["win_rate_percent"],
            "may_pnl": may_data["net_profit_pips"],
            "may_pf": may_data["profit_factor"],
            "june_trades": june_data["total_trades"],
            "june_win_rate": june_data["win_rate_percent"],
            "june_pnl": june_data["net_profit_pips"],
            "june_pf": june_data["profit_factor"],
        }
        results.append(res)
        print(f"Results: May P&L={res['may_pnl']:+.1f} (PF={res['may_pf']:.2f}), June P&L={res['june_pnl']:+.1f} (PF={res['june_pf']:.2f})", flush=True)

    # Print final table
    print("\n" + "="*80, flush=True)
    print("  TREND FILTER OPTIMIZATION RESULTS", flush=True)
    print("="*80, flush=True)
    print(f"{'Thresh':<8} | {'May Trades':<10} | {'May WR%':<8} | {'May P&L':<8} | {'May PF':<6} | {'June Trades':<11} | {'June WR%':<8} | {'June P&L':<8} | {'June PF':<6}", flush=True)
    print("-"*80, flush=True)
    for r in results:
        print(f"{r['threshold']:<8.1f} | {r['may_trades']:<10} | {r['may_win_rate']:<8.1f} | {r['may_pnl']:<8.1f} | {r['may_pf']:<6.2f} | {r['june_trades']:<11} | {r['june_win_rate']:<8.1f} | {r['june_pnl']:<8.1f} | {r['june_pf']:<6.2f}", flush=True)
    print("="*80, flush=True)

finally:
    restore()
