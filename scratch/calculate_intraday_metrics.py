import re
import numpy as np

report_path = r"d:\work\AxonAI\reports\intraday_bt_EURUSD_20260604_024013.md"

with open(report_path, "r", encoding="utf-8") as f:
    content = f.read()

# Pattern for 8 columns: ID | Direction | Entry Time | Entry | Exit Time | Exit | Pips | Signal
trade_pattern = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([+-]?\d+\.?\d*)[^|]*\|\s*([^|]+)\|",
    re.MULTILINE
)

trades = []
matches = trade_pattern.findall(content)

for m in matches:
    trade_id = int(m[0])
    direction = m[1].strip()
    entry_time_str = m[2].strip()
    pips = float(m[6].strip())
    signal = m[7].strip()
    
    trades.append({
        "id": trade_id,
        "direction": direction,
        "entry_time": entry_time_str,
        "pips": pips,
        "signal": signal
    })

print(f"Parsed {len(trades)} trades successfully.")

wins = [t["pips"] for t in trades if t["pips"] > 0]
losses = [t["pips"] for t in trades if t["pips"] <= 0]

gross_profit = sum(wins)
gross_loss = abs(sum(losses))
net_profit = gross_profit - gross_loss
win_rate = len(wins) / len(trades) if trades else 0

avg_win = np.mean(wins) if wins else 0
avg_loss = np.mean(losses) if losses else 0
pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

# Max Drawdown
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

# Sharpe Ratio
trade_pips_arr = np.array([t["pips"] for t in trades])
trade_sharpe = np.mean(trade_pips_arr) / np.std(trade_pips_arr) if np.std(trade_pips_arr) > 0 else 0
ann_sharpe = trade_sharpe * np.sqrt(252) # scaled to annual using sqrt of days

# Session PF
london_wins, london_losses = [], []
overlap_wins, overlap_losses = [], []
ny_wins, ny_losses = [], []

for t in trades:
    match = re.search(r"(\d{2}):\d{2}", t["entry_time"])
    hour = int(match.group(1)) if match else 0
    pips = t["pips"]
    
    # Session hours matching daemon/backtester:
    # London: 7:00 to 13:00 UTC
    # Overlap: 13:00 to 16:00 UTC
    # NY: 16:00 to 20:00 UTC
    if 7 <= hour < 13:
        if pips > 0: london_wins.append(pips)
        else: london_losses.append(pips)
    elif 13 <= hour < 16:
        if pips > 0: overlap_wins.append(pips)
        else: overlap_losses.append(pips)
    elif 16 <= hour <= 20:
        if pips > 0: ny_wins.append(pips)
        else: ny_losses.append(pips)

london_pf = sum(london_wins) / abs(sum(london_losses)) if sum(london_losses) != 0 else float('inf')
overlap_pf = sum(overlap_wins) / abs(sum(overlap_losses)) if sum(overlap_losses) != 0 else float('inf')
ny_pf = sum(ny_wins) / abs(sum(ny_losses)) if sum(ny_losses) != 0 else float('inf')

# Trigger PF
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

print("\n--- INTRADAY BACKTEST PERFORMANCE METRICS ---")
print(f"Profit Factor: {pf:.2f}")
print(f"Expectancy: {expectancy:.2f} pips")
print(f"Max Drawdown: {max_dd:.1f} pips")
print(f"Sharpe Ratio (Trade-level): {trade_sharpe:.3f}")
print(f"Sharpe Ratio (Annualized proxy): {ann_sharpe:.2f}")
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
