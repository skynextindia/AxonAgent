#!/usr/bin/env python3
"""Close the test position."""

import asyncio
import json
import websockets
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def close_position():
    uri = "ws://127.0.0.1:8766/"

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] Connected to execution bridge\n")

            close_request = {
                "action": "close",
                "position": 4269885901,
                "symbol": "EURUSD",
                "volume": 0.01,
                "type": 1,
                "price": 1.13398,
                "magic": 20260624,
                "deviation": 20
            }

            print("[SEND] Closing position 4269885901...")
            await websocket.send(json.dumps(close_request))

            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            result = json.loads(response)

            print(f"\n[OK] Close response:")
            print(json.dumps(result, indent=2))

            if result.get("success"):
                print(f"\n[SUCCESS] Position closed! Order: {result.get('order')}")
            else:
                print(f"\n[FAIL] Close failed: {result.get('reason')}")

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(close_position())
