# File: windows/execution_bridge.py
"""
MT5 Execution Bridge Service — runs on Windows, connects to a separate MetaTrader 5 terminal
(e.g., MetaQuotes MT5 for order execution), and exposes execution API via WebSocket.

Usage (from Windows cmd/powershell):
    python execution_bridge.py --port 8766 --path "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
"""

import asyncio
import json
import argparse
import sys
import os

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not found. Install with:")
    print("    pip install MetaTrader5")
    sys.exit(1)

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
except ImportError:
    print("ERROR: websockets package not found. Install with:")
    print("    pip install websockets")
    sys.exit(1)


# ── Global configuration ───────────────────────────────────────────
DEFAULT_PORT = 8766
terminal_path = None
login = None
password = None
server = None


# ── MT5 helpers ────────────────────────────────────────────────────

def mt5_init():
    """Initialize MT5 connection to the target execution terminal."""
    init_kwargs = {}
    if terminal_path:
        init_kwargs["path"] = terminal_path
    if login:
        init_kwargs["login"] = login
    if password:
        init_kwargs["password"] = password
    if server:
        init_kwargs["server"] = server

    print(f"Initializing MT5 with parameters: {init_kwargs}")
    if not mt5.initialize(**init_kwargs):
        err = mt5.last_error()
        print(f"MT5 initialize failed: {err}")
        return False

    info = mt5.terminal_info()
    if info:
        print(f"Connected to execution terminal: {info.name} (build {info.build})")
    
    acct = mt5.account_info()
    if acct:
        print(f"Execution Account: {acct.login}@{acct.server} balance={acct.balance:.2f} {acct.currency}")
    
    return True


def serialize_position(p):
    """Convert MT5 position object to dict."""
    return {
        "ticket": int(p.ticket),
        "symbol": p.symbol,
        "type": "BUY" if p.type == 0 else "SELL",  # mt5.POSITION_TYPE_BUY is 0, POSITION_TYPE_SELL is 1
        "volume": float(p.volume),
        "price_open": float(p.price_open),
        "price_current": float(p.price_current),
        "sl": float(p.sl),
        "tp": float(p.tp),
        "profit": float(p.profit),
        "magic": int(p.magic)
    }


# ── Client message handling ────────────────────────────────────────

async def handle_client(websocket):
    """Handle connection from daemon client."""
    remote = websocket.remote_address
    print(f"Client connected to execution bridge: {remote}")
    
    try:
        async for message in websocket:
            try:
                req = json.loads(message)
                req_type = req.get("action", "")
                
                print(f"Execution command: {req_type} from {remote}")
                
                if req_type == "open":
                    # Send order request
                    symbol = req.get("symbol")
                    order_type = int(req.get("type"))
                    volume = float(req.get("volume"))
                    price = float(req.get("price"))
                    sl = float(req.get("sl"))
                    tp = float(req.get("tp"))
                    magic = int(req.get("magic"))
                    deviation = int(req.get("deviation", 20))
                    
                    # Ensure symbol visibility
                    info = mt5.symbol_info(symbol)
                    if info and not info.visible:
                        mt5.symbol_select(symbol, True)
                    
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": volume,
                        "type": order_type,
                        "price": price,
                        "sl": sl,
                        "tp": tp,
                        "deviation": deviation,
                        "magic": magic,
                        "comment": "AxonAI Exec Bridge",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_FOK,
                    }
                    
                    print(f"Sending order to MetaTrader 5: {request}")
                    res = mt5.order_send(request)
                    
                    if res is None:
                        err = mt5.last_error()
                        response = {"success": False, "reason": f"mt5_internal_error: {err}"}
                    elif res.retcode != mt5.TRADE_RETCODE_DONE:
                        response = {
                            "success": False,
                            "reason": f"retcode_{res.retcode}",
                            "retcode": res.retcode,
                            "comment": getattr(res, "comment", "Unknown order failure")
                        }
                    else:
                        response = {
                            "success": True,
                            "order": int(res.order),
                            "volume": float(res.volume),
                            "price": float(res.price),
                            "sl": sl,
                            "tp": tp
                        }
                    
                    await websocket.send(json.dumps(response))
                    print(f"Order result sent to daemon: {response}")
                    
                elif req_type == "close":
                    ticket = int(req.get("position"))
                    symbol = req.get("symbol")
                    volume = float(req.get("volume"))
                    order_type = int(req.get("type"))
                    price = float(req.get("price"))
                    magic = int(req.get("magic"))
                    deviation = int(req.get("deviation", 20))
                    
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": volume,
                        "type": order_type,
                        "position": ticket,
                        "price": price,
                        "deviation": deviation,
                        "magic": magic,
                        "comment": "Exec Bridge Close",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    
                    print(f"Sending close order to MetaTrader 5: {request}")
                    res = mt5.order_send(request)
                    
                    if res is None:
                        err = mt5.last_error()
                        response = {"success": False, "reason": f"mt5_internal_error: {err}"}
                    elif res.retcode != mt5.TRADE_RETCODE_DONE:
                        response = {
                            "success": False,
                            "reason": f"retcode_{res.retcode}",
                            "retcode": res.retcode,
                            "comment": getattr(res, "comment", "Unknown close failure")
                        }
                    else:
                        response = {"success": True, "order": int(res.order)}
                    
                    await websocket.send(json.dumps(response))
                    print(f"Close result sent to daemon: {response}")
                    
                elif req_type == "modify":
                    ticket = int(req.get("position"))
                    symbol = req.get("symbol")
                    sl = float(req.get("sl"))
                    tp = float(req.get("tp"))
                    
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": ticket,
                        "symbol": symbol,
                        "sl": sl,
                        "tp": tp
                    }
                    
                    print(f"Modifying SL/TP: {request}")
                    res = mt5.order_send(request)
                    
                    if res is None:
                        err = mt5.last_error()
                        response = {"success": False, "reason": f"mt5_internal_error: {err}"}
                    elif res.retcode != mt5.TRADE_RETCODE_DONE:
                        response = {
                            "success": False,
                            "reason": f"retcode_{res.retcode}",
                            "retcode": res.retcode,
                            "comment": getattr(res, "comment", "Unknown SL/TP modification failure")
                        }
                    else:
                        response = {"success": True, "order": int(res.order)}
                    
                    await websocket.send(json.dumps(response))
                    print(f"Modification result sent to daemon: {response}")
                    
                elif req_type == "positions_get":
                    symbol = req.get("symbol")
                    magic = req.get("magic")
                    
                    if symbol:
                        positions = mt5.positions_get(symbol=symbol)
                    else:
                        positions = mt5.positions_get()
                        
                    pos_list = []
                    if positions:
                        for p in positions:
                            if magic is None or p.magic == int(magic):
                                pos_list.append(serialize_position(p))
                                
                    response = {"success": True, "positions": pos_list}
                    await websocket.send(json.dumps(response))

                elif req_type == "history_deals_get":
                    ticket = int(req.get("position"))
                    deals = mt5.history_deals_get(position=ticket)
                    deal_list = []
                    if deals:
                        for d in deals:
                            deal_list.append({
                                "ticket": int(d.ticket),
                                "position": int(getattr(d, "position_id", getattr(d, "position", 0))),
                                "entry": int(d.entry),
                                "type": int(d.type),
                                "price": float(d.price),
                                "volume": float(d.volume),
                                "time": int(d.time),
                                "profit": float(d.profit),
                                "comment": getattr(d, "comment", "")
                            })
                    response = {"success": True, "deals": deal_list}
                    await websocket.send(json.dumps(response))
                    
                elif req_type == "account_info":
                    acct = mt5.account_info()
                    if acct:
                        response = {
                            "success": True,
                            "balance": acct.balance,
                            "equity": acct.equity,
                            "profit": acct.profit,
                            "margin": acct.margin,
                            "free_margin": acct.margin_free,
                            "margin_level": getattr(acct, "margin_level", 0.0)
                        }
                    else:
                        response = {"success": False, "reason": "failed_to_get_account_info"}
                    await websocket.send(json.dumps(response))

                elif req_type == "symbol_info":
                    sym = req.get("symbol")
                    s_info = mt5.symbol_info(sym) if sym else None
                    if s_info:
                        response = {
                            "success": True,
                            "point": s_info.point,
                            "digits": s_info.digits,
                            "trade_tick_value": s_info.trade_tick_value,
                            "trade_tick_size": s_info.trade_tick_size,
                            # Broker volume constraints — required by 1%-lock sizing
                            # so bridge mode honours min/step/max like direct mode.
                            "volume_min": s_info.volume_min,
                            "volume_step": s_info.volume_step,
                            "volume_max": s_info.volume_max,
                        }
                    else:
                        response = {"success": False, "error": f"Could not get symbol info for {sym}"}
                    await websocket.send(json.dumps(response))
                    
                elif req_type == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                else:
                    await websocket.send(json.dumps({"success": False, "reason": "unknown_action"}))
                    
            except json.JSONDecodeError:
                print("Error: JSON decode failed for received message")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(f"Client disconnected from execution bridge: {remote}")


# ── HTTP health check ──────────────────────────────────────────────

async def http_handler(reader, writer):
    request = (await reader.read(1024)).decode("utf-8")
    if "GET /health" in request:
        response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
        response += json.dumps({
            "status": "ok",
            "type": "execution_bridge",
            "mt5_connected": mt5.terminal_info() is not None,
        })
    else:
        response = "HTTP/1.1 404 Not Found\r\n\r\n"
    writer.write(response.encode())
    await writer.drain()
    writer.close()


async def run_http_server(host, port):
    server = await asyncio.start_server(http_handler, host, port + 1)
    print(f"Health HTTP server on {host}:{port + 1}")
    async with server:
        await server.serve_forever()


# ── Main ───────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="MT5 Execution Bridge Service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="WebSocket port")
    parser.add_argument("--path", type=str, default=None, help="Path to terminal64.exe")
    parser.add_argument("--login", type=int, default=None, help="MT5 login")
    parser.add_argument("--password", type=str, default=None, help="MT5 password")
    parser.add_argument("--server", type=str, default=None, help="MT5 server name")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    global terminal_path, login, password, server
    terminal_path = args.path
    login = args.login
    password = args.password
    server = args.server

    if sys.platform == "win32":
        try:
            import ctypes
            flags = 0x80000000 | 0x00000001 | 0x00000040
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            print("  [+] Windows Sleep Prevention activated")
        except Exception as e:
            print(f"  [!] Failed to activate sleep prevention: {e}")

    print("=" * 60)
    print("  MT5 Execution Bridge Service")
    print("  Exposes trade execution API via WebSocket")
    print("=" * 60)

    if not mt5_init():
        print("FATAL: Could not initialize execution MT5")
        sys.exit(1)

    print(f"\nExecution Bridge: ws://{args.host}:{args.port}")
    print(f"Health check:     http://{args.host}:{args.port + 1}/health")
    print("Press Ctrl+C to stop.\n")

    async with ws_serve(handle_client, args.host, args.port):
        print(f"WebSocket server started on port {args.port}")
        await run_http_server(args.host, args.port)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExecution bridge stopped.")
    finally:
        mt5.shutdown()
