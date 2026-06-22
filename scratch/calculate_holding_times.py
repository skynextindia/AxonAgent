import re
from datetime import datetime

report_path = r"d:\work\AxonAI\reports\bridge_bt_EURUSD_12m_20260604_030725.md"

with open(report_path, "r", encoding="utf-8") as f:
    content = f.read()

# Pattern for 12 columns: ID | Dir | Entry Time | Entry Price | Exit Time | Exit Price | SL | TP | Pips | Signal | Exit Reason | Status
trade_pattern = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([+-]?\d+\.?\d*)[^|]*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\|",
    re.MULTILINE
)

matches = trade_pattern.findall(content)
win_durations = []
loss_durations = []

for m in matches:
    if m[0] == "ID":
        continue
    
    entry_time_str = m[2].strip()
    exit_time_str = m[4].strip()
    pips = float(m[8].strip())
    
    # Parse format YY-MM-DD HH:MM (or DD-MM-YY HH:MM)
    # The string looks like "23-06-25 07:04"
    try:
        entry_dt = datetime.strptime(entry_time_str, "%d-%m-%y %H:%M")
        exit_dt = datetime.strptime(exit_time_str, "%d-%m-%y %H:%M")
    except ValueError:
        # Fallback in case of different formatting
        try:
            entry_dt = datetime.strptime(entry_time_str, "%y-%m-%d %H:%M")
            exit_dt = datetime.strptime(exit_time_str, "%y-%m-%d %H:%M")
        except ValueError:
            continue
            
    duration_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
    
    if pips > 0:
        win_durations.append(duration_minutes)
    else:
        loss_durations.append(duration_minutes)

avg_win_duration = sum(win_durations) / len(win_durations) if win_durations else 0
avg_loss_duration = sum(loss_durations) / len(loss_durations) if loss_durations else 0

print("\n--- TRADE HOLDING TIMES ---")
print(f"Total Winning Trades: {len(win_durations)}")
print(f"Average Win Hold Time: {avg_win_duration:.1f} minutes ({avg_win_duration/60.0:.2f} hours)")
print(f"\nTotal Losing Trades: {len(loss_durations)}")
print(f"Average Loss Hold Time: {avg_loss_duration:.1f} minutes ({avg_loss_duration/60.0:.2f} hours)")
