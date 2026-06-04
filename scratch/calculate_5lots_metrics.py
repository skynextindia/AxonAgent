import re
import numpy as np

report_path = r"d:\work\AxonAI\reports\bridge_bt_EURUSD_12m_20260604_033715.md"

with open(report_path, "r", encoding="utf-8") as f:
    content = f.read()

# Pattern for 12 columns: ID | Dir | Entry Time | Entry Price | Exit Time | Exit Price | SL | TP | Pips | Signal | Exit Reason | Status
trade_pattern = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([+-]?\d+\.?\d*)[^|]*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\|",
    re.MULTILINE
)

matches = trade_pattern.findall(content)
monthly_trades = {}

for m in matches:
    if m[0] == "ID":
        continue
    entry_time = m[2].strip()
    pips = float(m[8].strip())
    
    # Extract Month (Format: DD-MM-YY -> 20YY-MM)
    parts = entry_time.split()[0].split('-')
    month = f"20{parts[2]}-{parts[1]}"
    
    # 5 standard lots = $50 per pip
    usd_pnl = pips * 50.0
    
    if month not in monthly_trades:
        monthly_trades[month] = []
    
    monthly_trades[month].append({
        "pips": pips,
        "usd_pnl": usd_pnl
    })

print("\n--- 12-MONTH MONTHLY BREAKDOWN (5 Standard Lots, $50/pip) ---")
starting_balance = 10000.0
balance = starting_balance

print(f"{'Month':<10} | {'Trades':<6} | {'Win Rate':<8} | {'Net P&L (USD)':<15} | {'Ending Balance':<15}")
print("-" * 65)

for month in sorted(monthly_trades.keys()):
    trades = monthly_trades[month]
    wins = [t["usd_pnl"] for t in trades if t["usd_pnl"] > 0]
    losses = [t["usd_pnl"] for t in trades if t["usd_pnl"] <= 0]
    
    win_rate = len(wins) / len(trades) if trades else 0.0
    net_pnl = sum(wins) + sum(losses)
    balance += net_pnl
    
    print(f"{month:<10} | {len(trades):<6} | {win_rate*100:5.1f}% | {f'${net_pnl:+,.2f}':<15} | ${balance:,.2f}")

# Full overall stats
all_trades = []
for m in monthly_trades.values():
    all_trades.extend(m)
all_wins = [t["usd_pnl"] for t in all_trades if t["usd_pnl"] > 0]
all_losses = [t["usd_pnl"] for t in all_trades if t["usd_pnl"] <= 0]

# Calculate max drawdown under 5 lots
balance_series = [starting_balance]
curr_bal = starting_balance
for t in all_trades:
    curr_bal += t["usd_pnl"]
    balance_series.append(curr_bal)
balance_series = np.array(balance_series)
running_max = np.maximum.accumulate(balance_series)
drawdowns = running_max - balance_series
max_dd_usd = np.max(drawdowns)
max_dd_pct = (max_dd_usd / running_max[np.argmax(drawdowns)]) * 100.0 if max_dd_usd > 0 else 0.0

print("-" * 65)
print(f"Starting Balance: ${starting_balance:,.2f}")
print(f"Ending Balance:   ${balance:,.2f}")
print(f"Total Net P&L:    ${balance - starting_balance:+,.2f} ({((balance - starting_balance)/starting_balance)*100:.1f}%)")
print(f"Max Drawdown:     ${max_dd_usd:,.2f} ({max_dd_pct:.2f}%)")
print(f"Profit Factor:    {sum(all_wins)/abs(sum(all_losses)):.2f}")
