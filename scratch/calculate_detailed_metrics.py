import re
import numpy as np

# Path to the backtest report markdown file
report_path = r"d:\work\AxonAI\reports\bridge_bt_EURUSD_12m_20260603_224524.md"

with open(report_path, "r", encoding="utf-8") as f:
    content = f.read()

# Regular expression to parse the trade log table
# Columns: ID | Dir | Entry Time | Entry Price | Exit Time | Exit Price | SL | TP | Pips | Signal | Exit Reason | Status |
# Example: | 1 | SELL | 23-06-25 07:04 | 1.15128 | 23-06-25 08:37 | 1.14916 | 1.15118 | 1.14916 | +21.2 | Bearish Microstructure Peak (microstructure_exhaustion) | 🎯 TP Hit | ✅ WIN |
trade_pattern = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([+-]?\d+\.?\d*)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\|",
    re.MULTILINE
)

trades = []
matches = trade_pattern.findall(content)

for m in matches:
    # Skip header/separator rows
    if m[0] == "ID":
        continue
    trade_id = int(m[0])
    direction = m[1].strip()
    entry_time_str = m[2].strip()
    pips = float(m[8].strip())
    signal = m[9].strip()
    status = m[11].strip()
    
    trades.append({
        "id": trade_id,
        "direction": direction,
        "entry_time": entry_time_str,
        "pips": pips,
        "signal": signal,
        "status": status
    })

print(f"Parsed {len(trades)} trades successfully.")

# Basic stats
wins = [t["pips"] for t in trades if t["pips"] > 0]
losses = [t["pips"] for t in trades if t["pips"] <= 0]

gross_profit = sum(wins)
gross_loss = abs(sum(losses))
net_profit = gross_profit - gross_loss
win_rate = len(wins) / len(trades) if trades else 0

avg_win = np.mean(wins) if wins else 0
avg_loss = np.mean(losses) if losses else 0
pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

# Expectancy
expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) # avg_loss is negative

# Max Drawdown & Recovery Factor
cumulative_pips = 0
cum_pips_series = []
for t in trades:
    cumulative_pips += t["pips"]
    cum_pips_series.append(cumulative_pips)

cum_pips_series = np.array(cum_pips_series)
running_max = np.maximum.accumulate(cum_pips_series)
drawdowns = running_max - cum_pips_series
max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0
recovery_factor = net_profit / max_dd if max_dd > 0 else float('inf')

# Sharpe Ratio (annualized, assuming 252 trading days/year)
trade_pips_arr = np.array([t["pips"] for t in trades])
# Standard trade-level Sharpe: mean / std
trade_sharpe = np.mean(trade_pips_arr) / np.std(trade_pips_arr) if np.std(trade_pips_arr) > 0 else 0
# Annualized Sharpe: (mean_pips * avg_trades_per_day / std_pips) * sqrt(252)
# Let's count trading days in dataset.
# The entry times span 12 months, e.g., 252 trading days.
ann_sharpe = trade_sharpe * np.sqrt(len(trades)) # standard simple Sharpe based on trade series

# Session PF
# Let's check session from entry time hour (UTC).
# Asian: 22 to 07 UTC (approx, but let's check actual hours: London open is 07, NY close is 20)
# London: 07:00 to 13:00
# Overlap: 13:00 to 16:00
# NY: 16:00 to 20:00
# Since the time in log is broker/local time or UTC? The report says entry time, let's parse hour from entry time.
# Entry times format in table: e.g. "23-06-25 07:04" -> Hour is 7.
# Let's group by Hour:
# London: hour in [7, 8, 9, 10, 11, 12]
# Overlap: hour in [13, 14, 15]
# NY: hour in [16, 17, 18, 19, 20]
# Others: rest
london_wins, london_losses = [], []
overlap_wins, overlap_losses = [], []
ny_wins, ny_losses = [], []
other_wins, other_losses = [], []

for t in trades:
    # time format: YY-MM-DD HH:MM
    match = re.search(r"\s+(\d{2}):", t["entry_time"])
    if match:
        hour = int(match.group(1))
    else:
        hour = 0
        
    pips = t["pips"]
    if hour in [7, 8, 9, 10, 11, 12]:
        if pips > 0: london_wins.append(pips)
        else: london_losses.append(pips)
    elif hour in [13, 14, 15]:
        if pips > 0: overlap_wins.append(pips)
        else: overlap_losses.append(pips)
    elif hour in [16, 17, 18, 19, 20]:
        if pips > 0: ny_wins.append(pips)
        else: ny_losses.append(pips)
    else:
        if pips > 0: other_wins.append(pips)
        else: other_losses.append(pips)

london_pf = sum(london_wins) / abs(sum(london_losses)) if sum(london_losses) != 0 else float('inf')
overlap_pf = sum(overlap_wins) / abs(sum(overlap_losses)) if sum(overlap_losses) != 0 else float('inf')
ny_pf = sum(ny_wins) / abs(sum(ny_losses)) if sum(ny_losses) != 0 else float('inf')

# Trigger PF (velocity_exhaustion vs microstructure_exhaustion)
vel_wins, vel_losses = [], []
micro_wins, micro_losses = [], []

for t in trades:
    sig = t["signal"].lower()
    pips = t["pips"]
    if "velocity_exhaustion" in sig:
        if pips > 0: vel_wins.append(pips)
        else: vel_losses.append(pips)
    elif "microstructure_exhaustion" in sig:
        if pips > 0: micro_wins.append(pips)
        else: micro_losses.append(pips)

vel_pf = sum(vel_wins) / abs(sum(vel_losses)) if sum(vel_losses) != 0 else float('inf')
micro_pf = sum(micro_wins) / abs(sum(micro_losses)) if sum(micro_losses) != 0 else float('inf')

print("\n--- RESULTS ---")
print(f"Profit Factor: {pf:.2f}")
print(f"Expectancy: {expectancy:.2f} pips")
print(f"Max Drawdown: {max_dd:.1f} pips")
print(f"Sharpe Ratio (Trade-level): {trade_sharpe:.3f}")
print(f"Sharpe Ratio (Annualized proxy): {ann_sharpe/np.sqrt(12):.2f}") # Ann Sharpe proxy over 12 months
print(f"Recovery Factor: {recovery_factor:.2f}")
print(f"Win Rate: {win_rate*100:.1f}%")
print(f"Average Win: {avg_win:.1f} pips")
print(f"Average Loss: {avg_loss:.1f} pips")
print(f"Session PF:")
print(f"  - London: {london_pf:.2f} (trades: {len(london_wins)+len(london_losses)})")
print(f"  - Overlap: {overlap_pf:.2f} (trades: {len(overlap_wins)+len(overlap_losses)})")
print(f"  - NY: {ny_pf:.2f} (trades: {len(ny_wins)+len(ny_losses)})")
print(f"Trigger PF:")
print(f"  - Rule A (Velocity Exhaustion): {vel_pf:.2f} (trades: {len(vel_wins)+len(vel_losses)})")
print(f"  - Rule B (Microstructure Exhaustion): {micro_pf:.2f} (trades: {len(micro_wins)+len(micro_losses)})")
