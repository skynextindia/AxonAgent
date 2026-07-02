#!/usr/bin/env python3
"""Force close any open positions and verify journal updates."""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from axonai.dataflows.mt5_order_bridge import send_order_via_bridge, get_positions_via_bridge

config = {
    "mt5_trade_terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
}

print("Fetching open positions...")
positions = get_positions_via_bridge("C:\\Program Files\\MetaTrader 5\\terminal64.exe", "EURUSD")

if not positions:
    print("[ERROR] No open positions found")
    sys.exit(1)

print(f"[OK] Found positions: {positions}")

if isinstance(positions, dict):
    positions = positions.get("positions", [])

for pos in positions:
    ticket = pos['ticket']
    print(f"\n  Ticket {ticket}: {pos['type']} {pos['volume']} @ {pos['price_open']:.5f}")
    print(f"  Current P&L: {pos.get('profit', 0):.2f}")

    print(f"  -> Closing ticket {ticket}...")
    result = send_order_via_bridge(config, {
        "action": "close",
        "position": ticket,
        "symbol": "EURUSD",
    })

    if result and result.get("success"):
        print(f"  [OK] Close executed")
    else:
        print(f"  [ERROR] Close failed: {result}")

print("\n" + "="*60)
print("Waiting 2 seconds for daemon to detect closure...")
time.sleep(2)

# Check if journal was updated
print("\nChecking journal...")
reports_dir = Path("reports")
if reports_dir.exists():
    jsonl = reports_dir / "signals.jsonl"
    log = reports_dir / "signals.log"

    if jsonl.exists():
        with open(jsonl) as f:
            all_lines = f.readlines()
            last_line = all_lines[-1] if all_lines else None
        print(f"[OK] signals.jsonl exists ({jsonl.stat().st_size} bytes)")
        if last_line and "trade_closed" in str(last_line):
            print(f"  [OK] Last entry is TRADE_CLOSED")
        else:
            print(f"  [WARN] Last entry type unknown")

    if log.exists():
        print(f"[OK] signals.log exists ({log.stat().st_size} bytes)")
        with open(log) as f:
            lines = f.readlines()[-3:]
        for line in lines:
            print(f"  {line.strip()}")
else:
    print("[ERROR] reports/ directory not found")
