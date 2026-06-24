#!/usr/bin/env python3
"""Send a test order from Exness (data) to MetaQuotes (execution) via bridge."""

import asyncio
import json
import websockets
import sys
import io

# Set UTF-8 encoding for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def send_test_order():
    uri = "ws://127.0.0.1:8766/"

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] Connected to execution bridge at ws://127.0.0.1:8766")

            # Send a BUY order: 0.01 lot, EURUSD at market price
            # type: 0 = BUY, 1 = SELL
            order_request = {
                "action": "open",
                "symbol": "EURUSD",
                "type": 0,
                "volume": 0.01,
                "price": 1.0850,
                "sl": 0.0,
                "tp": 0.0,
                "magic": 20260624,
                "deviation": 20
            }

            print(f"\n[SEND] Sending order: {json.dumps(order_request, indent=2)}")
            await websocket.send(json.dumps(order_request))

            # Wait for response
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            result = json.loads(response)

            print(f"\n[OK] Response from MetaQuotes terminal:")
            print(json.dumps(result, indent=2))

            if result.get("success"):
                print(f"\n[SUCCESS] ORDER EXECUTED! Ticket: {result.get('ticket')}")
                print(f"   Check MetaQuotes terminal for position")
            else:
                print(f"\n[FAIL] Order failed: {result.get('error', 'Unknown error')}")

            return result.get("success", False)

    except Exception as e:
        print(f"[ERROR] {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(send_test_order())
    sys.exit(0 if success else 1)
