# File: scratch/calculate_gold_trade_entries.py
import json
import os
import sys
import pandas as pd

def parse_snapshots(snapshot_file):
    records = {}
    if not os.path.exists(snapshot_file):
        return records
        
    print("Reading snapshots to memory...")
    with open(snapshot_file, "r") as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 24:
                continue
            ts_str = parts[0]
            # Match by second (YYYY-MM-DD HH:MM:SS)
            if len(ts_str) >= 19:
                sec_key = ts_str[:19]
                try:
                    records[sec_key] = {
                        "vel_pct": float(parts[2]),
                        "decay_ratio": float(parts[5]),
                        "tick_eff": float(parts[7])
                    }
                except ValueError:
                    pass
    return records

def run_analysis():
    signals_file = "reports/signals.jsonl"
    snapshot_file = "reports/engine_snapshots_XAUUSD.csv"
    
    if not os.path.exists(signals_file):
        print(f"Error: {signals_file} not found")
        return
        
    snap_records = parse_snapshots(snapshot_file)
    print(f"Loaded {len(snap_records)} unique snapshot seconds.")
    
    trades = []
    with open(signals_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("type") == "trade_closed" and data.get("symbol") == "XAUUSD":
                    trades.append(data)
            except Exception:
                pass
                
    print(f"Found {len(trades)} XAUUSD closed trades.")
    
    results = []
    passed_layer4 = 0
    passed_wins = 0
    failed_layer4 = 0
    failed_wins = 0
    
    min_date = None
    max_date = None
    
    for t in trades:
        entry_time_str = t.get("entry_time")
        if not entry_time_str:
            continue
            
        t_date = entry_time_str[:10]
        if min_date is None or t_date < min_date:
            min_date = t_date
        if max_date is None or t_date > max_date:
            max_date = t_date
            
        # Match by second YYYY-MM-DD HH:MM:SS
        sec_key = entry_time_str[:19]
        snap = snap_records.get(sec_key)
        
        # If not exact match, check +/- 1 second
        if not snap:
            try:
                dt = pd.to_datetime(sec_key)
                for offset in [-1, 1]:
                    alt_key = (dt + pd.Timedelta(seconds=offset)).strftime("%Y-%m-%d %H:%M:%S")
                    if alt_key in snap_records:
                        snap = snap_records[alt_key]
                        break
            except Exception:
                pass
                
        if not snap:
            continue
            
        vel_pct = snap["vel_pct"]
        decay = snap["decay_ratio"]
        eff = snap["tick_eff"]
        outcome = t["outcome"]
        pips = float(t["pips"])
        
        results.append({
            "ticket": t["ticket"],
            "entry_time": entry_time_str,
            "vel_pct": vel_pct,
            "decay": decay,
            "eff": eff,
            "outcome": outcome,
            "pips": pips
        })
        
        # Layer 4 Rule: vel <= 30%, decay >= 0.40, eff <= 0.30
        passed = (vel_pct <= 30.0) and (decay >= 0.40) and (eff <= 0.30)
        
        if passed:
            passed_layer4 += 1
            if outcome == "WIN":
                passed_wins += 1
        else:
            failed_layer4 += 1
            if outcome == "WIN":
                failed_wins += 1

    print("\n--- INDIVIDUAL TRADE DETAIL ---")
    print(f"{'Ticket':<15} | {'Entry Time':<19} | {'Vel %':<6} | {'Decay':<5} | {'Eff':<5} | {'Outcome':<7} | {'Pips':<6}")
    print("-" * 80)
    for r in results:
        print(f"{r['ticket']:<15} | {r['entry_time']:<19} | {r['vel_pct']:<6.1f} | {r['decay']:<5.2f} | {r['eff']:<5.2f} | {r['outcome']:<7} | {r['pips']:+6.1f}")
        
    print("\n--- SUMMARY METRICS ---")
    print(f"Total Matched Trades: {len(results)}")
    print(f"Passed Layer 4: {passed_layer4}")
    if passed_layer4 > 0:
        print(f"  Passed Win Rate: {passed_wins / passed_layer4 * 100:.1f}%")
    else:
        print("  Passed Win Rate: N/A")
        
    print(f"Failed Layer 4: {failed_layer4}")
    if failed_layer4 > 0:
        print(f"  Failed Win Rate: {failed_wins / failed_layer4 * 100:.1f}%")
    else:
        print("  Failed Win Rate: N/A")
        
    print(f"\nSignals Date Range: {min_date} to {max_date}")

if __name__ == "__main__":
    run_analysis()
