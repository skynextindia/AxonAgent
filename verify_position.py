#!/usr/bin/env python3
"""Query open positions from MetaQuotes terminal."""

import asyncio
import json
import websockets
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def verify_position():
    uri = "ws://127.0.0.1:8766/"

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] Connected to execution bridge")

            # Query all positions
            query = {
                "action": "positions_get",
                "symbol": "EURUSD"
            }

            print(f"\n[QUERY] Getting all EURUSD positions...")
            await websocket.send(json.dumps(query))

            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            result = json.loads(response)

            print(f"\n[OK] Positions response:")
            print(json.dumps(result, indent=2))

            if result.get("success"):
                positions = result.get("positions", [])
                if positions:
                    print(f"\n[OK] Found {len(positions)} position(s) in MetaQuotes terminal:")
                    for pos in positions:
                        print(f"\n  Ticket: {pos['ticket']}")
                        print(f"  Type: {pos['type']}")
                        print(f"  Volume: {pos['volume']} lots")
                        print(f"  Entry Price: {pos['price_open']}")
                        print(f"  Current Price: {pos['price_current']}")
                        print(f"  Profit: {pos['profit']}")
                else:
                    print(f"\n[WARNING] No open positions found")
            else:
                print(f"\n[ERROR] Query failed")

            return len(result.get("positions", [])) > 0

    except Exception as e:
        print(f"[ERROR] {e}")
        return False

if __name__ == "__main__":
    asyncio.run(verify_position())
