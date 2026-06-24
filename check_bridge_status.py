#!/usr/bin/env python3
"""Check what account the execution bridge is connected to."""

import asyncio
import json
import websockets
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def check_status():
    uri = "ws://127.0.0.1:8766/"

    try:
        async with websockets.connect(uri) as websocket:
            print("[OK] Connected to execution bridge\n")

            # Query account info
            query = {
                "action": "account_info"
            }

            print("[QUERY] Getting bridge account info...")
            await websocket.send(json.dumps(query))

            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            result = json.loads(response)

            print(f"\n[OK] Bridge is connected to this account:\n")
            print(json.dumps(result, indent=2))

            if result.get("success"):
                print(f"\n>>> Bridge Server: {result.get('server')}")
                print(f">>> Bridge Login: {result.get('login')}")
                print(f">>> Bridge Balance: {result.get('balance')}")
                print(f"\nCHECK: Does this match YOUR MetaQuotes terminal account?")
                print(f"       If NOT, the position is in a different account.")
            else:
                print(f"\n[ERROR] Could not get account info: {result}")

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(check_status())
