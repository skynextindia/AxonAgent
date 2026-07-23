# File: scratch/analyze_exits.py
import json
import os
import sys
import pandas as pd
from datetime import datetime

def parse_line_defensively(line):
    # Splits by comma and strips values
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 24:
        return None
        
    try:
        ts = pd.to_datetime(parts[0])
        price = float(parts[1])
        vel_pct = float(parts[2])
        decay_ratio = float(parts[5])
        
        # Check index dynamically depending on column count (25 vs 33)
        # col 7 is tick_eff in both versions
        tick_eff = float(parts[7])
        
        # In old 25-col version:
        # col 16 is disp_class, col 23 is reversal_pressure
        # In new 33-col version:
        # col 16 is disp_class, col 23 is reversal_pressure
        # Let's map directly:
        disp_class = parts[16]
        reversal_pressure = float(parts[23])
        
        return {
            "timestamp": ts,
            "price": price,
            "vel_pct": vel_pct,
            "decay_ratio": decay_ratio,
            "tick_eff": tick_eff,
            "disp_class": disp_class,
            "reversal_pressure": reversal_pressure
        }
    except Exception:
        return None

def analyze_trade_snapshots():
    signals_file = "reports/signals.jsonl"
    if not os.path.exists(signals_file):
        print(f"Error: {signals_file} not found.")
        return

    # 1. Load closed trades
    trades = []
    with open(signals_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                if data.get("type") == "trade_closed" and data.get("outcome") in ["WIN", "LOSS"]:
                    trades.append(data)
            except Exception:
                pass

    if not trades:
        print("No closed WIN/LOSS trades found in signals.jsonl.")
        return

    print(f"Loaded {len(trades)} closed trades. Analyzing lifecycles...")

    # Group trades by symbol
    df_trades = pd.DataFrame(trades)
    
    for symbol, sym_trades in df_trades.groupby("symbol"):
        snapshot_file = f"reports/engine_snapshots_{symbol}.csv"
        if not os.path.exists(snapshot_file):
            print(f"No snapshot CSV file found for {symbol} at {snapshot_file}. Skipping.")
            continue

        print(f"\n==========================================")
        print(f" ANALYZING EXITS FOR {symbol}")
        print(f"==========================================")
        
        # Load snapshots line-by-line defensively to handle 25 vs 33 column shifts
        parsed_records = []
        try:
            with open(snapshot_file, "r") as sf:
                for line in sf:
                    rec = parse_line_defensively(line)
                    if rec:
                        parsed_records.append(rec)
            
            if not parsed_records:
                print(f"No valid snapshot records found for {symbol}.")
                continue
                
            df_snap = pd.DataFrame(parsed_records)
            print(f"Loaded {len(df_snap)} snapshot ticks.")
        except Exception as e:
            print(f"Failed to load {snapshot_file}: {e}")
            continue

        loss_adverse_ticks = []
        win_peak_ticks = []
        
        # Limit to the last 20 trades per symbol to speed up scan and focus on recent optimized behavior
        recent_trades = sym_trades.tail(20)
        
        for _, trade in recent_trades.iterrows():
            exit_time = pd.to_datetime(trade["timestamp"])
            # Signals has exit time. Look back 30 minutes for active ticks.
            trade_ticks = df_snap[(df_snap["timestamp"] >= exit_time - pd.Timedelta(minutes=30)) & (df_snap["timestamp"] <= exit_time)]
            
            if trade_ticks.empty:
                continue

            outcome = trade["outcome"]
            pips = float(trade["pips"])
            direction = trade["direction"]

            # Filter ticks by price movement relative to entry
            entry_price = float(trade["entry_price"])
            if outcome == "LOSS":
                # Find ticks that went adverse (against the trade)
                if direction == "BUY":
                    adverse = trade_ticks[trade_ticks["price"] < entry_price]
                else:
                    adverse = trade_ticks[trade_ticks["price"] > entry_price]
                
                if not adverse.empty:
                    # Capture peak adverse metrics
                    peak_idx = adverse["vel_pct"].idxmax()
                    loss_adverse_ticks.append({
                        "ticket": trade["ticket"],
                        "vel_pct": adverse.loc[peak_idx, "vel_pct"],
                        "decay_ratio": adverse.loc[peak_idx, "decay_ratio"],
                        "tick_eff": adverse.loc[peak_idx, "tick_eff"],
                        "disp_class": adverse.loc[peak_idx, "disp_class"],
                    })
            elif outcome == "WIN":
                # Find ticks that went favorable (in direction of the trade)
                if direction == "BUY":
                    favorable = trade_ticks[trade_ticks["price"] > entry_price]
                else:
                    favorable = trade_ticks[trade_ticks["price"] < entry_price]
                
                if not favorable.empty:
                    # Capture metrics at peak price (MFE)
                    if direction == "BUY":
                        peak_idx = favorable["price"].idxmax()
                    else:
                        peak_idx = favorable["price"].idxmin()
                    
                    win_peak_ticks.append({
                        "ticket": trade["ticket"],
                        "vel_pct": favorable.loc[peak_idx, "vel_pct"],
                        "decay_ratio": favorable.loc[peak_idx, "decay_ratio"],
                        "tick_eff": favorable.loc[peak_idx, "tick_eff"],
                        "disp_class": favorable.loc[peak_idx, "disp_class"],
                    })

        # Output Summary
        if loss_adverse_ticks:
            df_loss = pd.DataFrame(loss_adverse_ticks)
            print("\n--- LOSING TRADES: Opposing Ticks Metrics ---")
            print(df_loss.describe())
            print("\nOpposing Displacement Class Distribution on Losses:")
            print(df_loss["disp_class"].value_counts())

        if win_peak_ticks:
            df_win = pd.DataFrame(win_peak_ticks)
            print("\n--- WINNING TRADES: Peak (MFE) Ticks Metrics ---")
            print(df_win.describe())
            print("\nDisplacement Class Distribution at Profit Peaks:")
            print(df_win["disp_class"].value_counts())

if __name__ == "__main__":
    analyze_trade_snapshots()
