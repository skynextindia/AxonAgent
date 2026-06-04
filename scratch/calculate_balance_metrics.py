import re
import numpy as np

report_path = r"d:\work\AxonAI\reports\bridge_bt_EURUSD_12m_20260604_030725.md"

with open(report_path, "r", encoding="utf-8") as f:
    content = f.read()

# Pattern for 12 columns: ID | Dir | Entry Time | Entry Price | Exit Time | Exit Price | SL | TP | Pips | Signal | Exit Reason | Status
trade_pattern = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([+-]?\d+\.?\d*)[^|]*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\|",
    re.MULTILINE
)

trades = []
matches = trade_pattern.findall(content)

for m in matches:
    if m[0] == "ID":
        continue
    trade_id = int(m[0])
    pips = float(m[8].strip())
    
    # 1 standard lot on EURUSD = $10 per pip
    usd_pnl = pips * 10.0
    
    trades.append({
        "id": trade_id,
        "pips": pips,
        "usd_pnl": usd_pnl
    })

starting_balance = 10000.0
balance = starting_balance
balance_series = [starting_balance]

wins_usd = []
losses_usd = []

for t in trades:
    balance += t["usd_pnl"]
    balance_series.append(balance)
    if t["usd_pnl"] > 0:
        wins_usd.append(t["usd_pnl"])
    else:
        losses_usd.append(t["usd_pnl"])

balance_series = np.array(balance_series)
running_max = np.maximum.accumulate(balance_series)
drawdowns_usd = running_max - balance_series
max_dd_usd = np.max(drawdowns_usd)
max_dd_pct = (max_dd_usd / running_max[np.argmax(drawdowns_usd)]) * 100.0 if max_dd_usd > 0 else 0.0

gross_profit_usd = sum(wins_usd)
gross_loss_usd = abs(sum(losses_usd))
net_profit_usd = gross_profit_usd - gross_loss_usd
win_rate = len(wins_usd) / len(trades) if trades else 0

avg_win_usd = np.mean(wins_usd) if wins_usd else 0
avg_loss_usd = np.mean(losses_usd) if losses_usd else 0
recovery_factor = net_profit_usd / max_dd_usd if max_dd_usd > 0 else float('inf')

print("\n--- 12-MONTH BALANCE-BASED PERFORMANCE METRICS ($10k Starting Balance, 1 Standard Lot) ---")
print(f"Starting Balance: ${starting_balance:,.2f}")
print(f"Ending Balance:   ${balance:,.2f}")
print(f"Net Profit:       ${net_profit_usd:+,.2f} ({ (net_profit_usd / starting_balance) * 100.0:+.1f}%)")
print(f"Profit Factor:    {gross_profit_usd / gross_loss_usd:.2f}")
print(f"Win Rate:         {win_rate*100:.1f}%")
print(f"Total Trades:     {len(trades)}")
print(f"Average Win:      ${avg_win_usd:,.2f}")
print(f"Average Loss:     ${avg_loss_usd:,.2f}")
print(f"Max Drawdown:     ${max_dd_usd:,.2f} ({max_dd_pct:.2f}%)")
print(f"Recovery Factor:  {recovery_factor:.2f}")
