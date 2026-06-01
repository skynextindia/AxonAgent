"""
MT5 Bridge Service — runs on Windows, connects to MetaTrader 5,
and exposes live & historical data to the WSL dashboard via WebSocket + HTTP.

Usage (from Windows cmd/powershell):
    python mt5_bridge.py --port 8765

The WSL dashboard connects to this bridge when running in WSL mode.
"""

import asyncio
import json
import time
import argparse
import sys
import os
import math
import struct
from datetime import datetime, timedelta
from collections import deque

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


# ── Configuration ──────────────────────────────────────────────────
DEFAULT_PORT = 8765
UPDATE_INTERVAL = 0.1        # 100ms for ticks
SLOW_INTERVAL = 2.0          # regime/candles
ACCOUNT_INTERVAL = 5.0       # account info
LEVELS_INTERVAL = 10.0       # support/resistance
CANDLES_INTERVAL = 15.0      # candle data

# ── Global state ───────────────────────────────────────────────────
connected_clients = set()
latest_state = {}
symbol = "EURUSD"
broker_offset = 0


# ── MT5 helpers ────────────────────────────────────────────────────

def mt5_init():
    global broker_offset
    if not mt5.initialize():
        err = mt5.last_error()
        print(f"MT5 initialize failed: {err}")
        return False
    info = mt5.terminal_info()
    print(f"MT5 terminal: {info.name} build {info.build}")
    acct = mt5.account_info()
    if acct:
        print(f"Account: {acct.login}@{acct.server} balance={acct.balance:.2f} {acct.currency}")
    # Compute broker offset
    try:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            server_sec = int(tick.time)
            local_sec = int(time.time())
            broker_offset = server_sec - local_sec
            print(f"Broker time offset: {broker_offset}s")
    except Exception:
        pass
    return True


def ensure_symbol(sym):
    info = mt5.symbol_info(sym)
    if info is None:
        print(f"Symbol {sym} not found")
        return False
    if not info.visible:
        mt5.symbol_select(sym, True)
    return True


def get_tick_data(sym):
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        return None
    bid = round(float(tick.bid), 5)
    ask = round(float(tick.ask), 5)
    spread = round(float(ask - bid), 5)
    spread_pips = round(spread * 10000, 1)
    return {
        "type": "tick",
        "symbol": sym,
        "bid": bid,
        "ask": ask,
        "spread": spread_pips,
        "time": int(tick.time),
        "tick_velocity": 0.0,
        "tick_imbalance_10s": 0.0,
        "tick_imbalance_60s": 0.0,
        "tick_imbalance_300s": 0.0,
        "tick_spread_delta": 0.0,
        "tick_collapse": False,
        "tick_agg_shift": False,
        "tick_absorption": False,
    }


def get_account_data(sym):
    acct = mt5.account_info()
    if not acct:
        return None
    positions = mt5.positions_get(symbol=sym) or []
    pos_list = []
    for p in positions:
        pos_list.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "buy" if p.type == 0 else "sell",
            "volume": p.volume,
            "price_open": round(p.price_open, 5),
            "price_current": round(p.price_current, 5),
            "sl": round(p.sl, 5) if p.sl else 0,
            "tp": round(p.tp, 5) if p.tp else 0,
            "profit": round(p.profit, 2),
        })
    bal = float(getattr(acct, 'balance', 0) or 0)
    eq = float(getattr(acct, 'equity', 0) or 0)
    prof = float(getattr(acct, 'profit', 0) or 0)
    marg = float(getattr(acct, 'margin', 0) or 0)
    free_marg = float(getattr(acct, 'margin_free', 0) or 0)
    marg_lvl = float(getattr(acct, 'margin_level', 0) or 0)
    return {
        "type": "account",
        "balance": round(bal, 2),
        "equity": round(eq, 2),
        "profit": round(prof, 2),
        "margin": round(marg, 2),
        "free_margin": round(free_marg, 2),
        "margin_level": round(marg_lvl, 2) if marg_lvl else 0,
        "positions": pos_list,
    }


def get_regime_data(sym):
    """Compute market regime from recent MT5 data."""
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 48)
    atr_val = 0.0
    volatility = "MODERATE"
    regime = "SIDEWAYS"
    trend = "sideways"
    belief = 0.0

    if rates is not None and len(rates) > 20:
        closes = [r[4] for r in rates]
        highs = [r[2] for r in rates]
        lows = [r[3] for r in rates]

        # ATR (14-period)
        tr_values = []
        for i in range(1, min(15, len(rates))):
            hl = highs[-i] - lows[-i]
            hc = abs(highs[-i] - closes[-i-1])
            lc = abs(lows[-i] - closes[-i-1])
            tr_values.append(max(hl, hc, lc))
        atr_val = sum(tr_values) / len(tr_values) if tr_values else 0

        # Volatility
        atr_pct = atr_val / closes[-1] * 100 if closes[-1] else 0
        if atr_pct < 0.1:
            volatility = "LOW"
        elif atr_pct < 0.3:
            volatility = "MODERATE"
        else:
            volatility = "HIGH"

        # Trend detection (EMA cross)
        ema9 = sum(closes[-9:]) / 9
        ema21 = sum(closes[-21:]) / 21
        ema50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema21

        if ema9 > ema21 > ema50:
            trend = "up"
            regime = "TRENDING"
            belief = min(1.0, (ema9 - ema21) / (atr_val + 0.0001) * 0.5)
        elif ema9 < ema21 < ema50:
            trend = "down"
            regime = "TRENDING"
            belief = max(-1.0, (ema9 - ema21) / (atr_val + 0.0001) * 0.5)
        else:
            # Check if ranging or volatile
            max_close = max(closes[-20:])
            min_close = min(closes[-20:])
            range_pct = (max_close - min_close) / min_close * 100 if min_close else 0
            if range_pct > 1.5:
                regime = "VOLATILE"
            else:
                regime = "RANGING"
            belief = (ema9 - ema21) / (atr_val + 0.0001) * 0.3
            belief = max(-0.5, min(0.5, belief))

    tick = mt5.symbol_info_tick(sym)
    spread_pips = (tick.ask - tick.bid) * 10000 if tick else 0

    return {
        "type": "regime",
        "symbol": sym,
        "dominant": regime,
        "confidence": round(abs(belief) + 0.3, 2),
        "volatility": volatility,
        "atr": round(atr_val, 5),
        "spread_pips": round(spread_pips, 1),
        "spread_safe": spread_pips < 3.0,
        "belief": round(belief, 2),
        "market_closed": False,
        "cooldown_remaining": 0,
        "events_detected": 0,
        "events_fired": 0,
        "events_skipped": 0,
        "trend_h4": trend,
        "trend_h1": trend,
        "trend_m15": trend,
        "london_open_bias": "NEUTRAL",
        "london_range_high": 0,
        "london_range_low": 0,
        "eur_strength": 0,
        "usd_strength": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_total": 0,
        "llm_calls": 0,
        "tool_calls": 0,
    }


def get_candles_data(sym, timeframe="M15", count=100):
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe, mt5.TIMEFRAME_M15)
    rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
    if rates is None:
        return None
    candles = []
    for r in rates:
        candles.append({
            "time": int(r[0]),
            "open": round(r[1], 5),
            "high": round(r[2], 5),
            "low": round(r[3], 5),
            "close": round(r[4], 5),
            "tick_volume": int(r[5]),
        })
    return {"type": "candles", "timeframe": timeframe, "candles": candles}


def get_levels_data(sym):
    """Compute support/resistance levels from M15/H1 data."""
    levels = []
    for tf_name, tf_const, lookback in [
        ("H1", mt5.TIMEFRAME_H1, 24),
        ("M15", mt5.TIMEFRAME_M15, 96),
    ]:
        rates = mt5.copy_rates_from_pos(sym, tf_const, 0, lookback)
        if rates is None or len(rates) < 5:
            continue
        highs = [r[2] for r in rates]
        lows = [r[3] for r in rates]

        # Find swing highs/lows
        for i in range(2, len(rates) - 2):
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and \
               highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                levels.append({
                    "price": round(highs[i], 5),
                    "level_type": f"{tf_name}_SWING",
                    "direction": "resistance",
                    "strength": 0.7,
                    "touches": 2,
                    "timeframe": tf_name,
                })
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and \
               lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                levels.append({
                    "price": round(lows[i], 5),
                    "level_type": f"{tf_name}_SWING",
                    "direction": "support",
                    "strength": 0.7,
                    "touches": 2,
                    "timeframe": tf_name,
                })
    return {"type": "levels", "price_levels": levels[:12]}  # max 12 levels


def get_historical_bars(sym, timeframe, start, end):
    """Fetch historical bars for backtesting."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1,
    }
    tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
    rates = mt5.copy_rates_range(sym, tf, start, end)
    # Fallback: copy_rates_from_pos works even when copy_rates_range
    # returns None (e.g. symbol not fully synced for that timeframe)
    if rates is None or len(rates) == 0:
        count = 168  # 7 days of hourly = 168, scale proportionally
        if timeframe == "M1":
            count = 10080
        elif timeframe == "M5":
            count = 2016
        elif timeframe == "M15":
            count = 672
        elif timeframe == "M30":
            count = 336
        elif timeframe == "H4":
            count = 42
        elif timeframe == "D1":
            count = 7
        elif timeframe == "W1":
            count = 1
        rates = mt5.copy_rates_from_pos(sym, tf, 0, count)
        if rates is None:
            return []
    result = []
    for r in rates:
        result.append({
            "time": int(r[0]),
            "open": round(r[1], 5),
            "high": round(r[2], 5),
            "low": round(r[3], 5),
            "close": round(r[4], 5),
            "tick_volume": int(r[5]),
            "spread": int(r[6]),
            "real_volume": int(r[7]),
        })
    return result


# ── WebSocket server ───────────────────────────────────────────────

async def broadcast(msg_dict):
    """Send JSON message to all connected clients."""
    global connected_clients
    if not connected_clients:
        return
    msg = json.dumps(msg_dict)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    connected_clients -= dead


async def handle_client(websocket):
    """Handle a WebSocket connection."""
    global connected_clients, symbol
    connected_clients.add(websocket)
    remote = websocket.remote_address
    print(f"Client connected: {remote}")
    try:
        # Send current state immediately
        if latest_state.get("tick"):
            await websocket.send(json.dumps(latest_state["tick"]))
        if latest_state.get("regime"):
            await websocket.send(json.dumps(latest_state["regime"]))
        if latest_state.get("account"):
            await websocket.send(json.dumps(latest_state["account"]))
        if latest_state.get("candles"):
            await websocket.send(json.dumps(latest_state["candles"]))
        if latest_state.get("levels"):
            await websocket.send(json.dumps(latest_state["levels"]))

        async for message in websocket:
            try:
                req = json.loads(message)
                req_type = req.get("type", "")
                if req_type == "get_historical":
                    bars = get_historical_bars(
                        req.get("symbol", symbol),
                        req.get("timeframe", "H1"),
                        datetime.fromtimestamp(req["from"]),
                        datetime.fromtimestamp(req["to"]),
                    )
                    await websocket.send(json.dumps({
                        "type": "historical",
                        "request_id": req.get("request_id", 0),
                        "symbol": req.get("symbol", symbol),
                        "timeframe": req.get("timeframe", "H1"),
                        "bars": bars,
                    }))
                elif req_type == "switch_pair":
                    new_symbol = req.get("symbol", req.get("mt5", ""))
                    if new_symbol:
                        symbol = new_symbol
                        # Immediately fetch and broadcast all data for the new symbol
                        if ensure_symbol(symbol):
                            tick = get_tick_data(symbol)
                            if tick:
                                latest_state["tick"] = tick
                                await broadcast(tick)
                            regime = get_regime_data(symbol)
                            if regime:
                                latest_state["regime"] = regime
                                await broadcast(regime)
                            acct = get_account_data(symbol)
                            if acct:
                                latest_state["account"] = acct
                                await broadcast(acct)
                            candles = get_candles_data(symbol)
                            if candles:
                                latest_state["candles"] = candles
                                await broadcast(candles)
                            levels = get_levels_data(symbol)
                            if levels:
                                latest_state["levels"] = levels
                                await broadcast(levels)
                        print(f"Switched to symbol: {symbol}")
                        await websocket.send(json.dumps({
                            "type": "switch_ack",
                            "symbol": symbol,
                        }))
                elif req_type == "get_symbols":
                    # List available symbols
                    symbols_list = []
                    all_syms = mt5.symbols_total()
                    if all_syms and all_syms > 0:
                        sym_info = mt5.symbols_get()
                        if sym_info:
                            for s in sym_info[:200]:  # limit
                                symbols_list.append({
                                    "name": s.name,
                                    "description": s.description,
                                    "digits": s.digits,
                                })
                    await websocket.send(json.dumps({
                        "type": "symbols_list",
                        "symbols": symbols_list,
                    }))
                elif req_type == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"Client disconnected: {remote}")


# ── Data update loop ───────────────────────────────────────────────

async def data_loop():
    """Main data acquisition and broadcast loop."""
    global latest_state
    last_slow = 0.0
    last_account = 0.0
    last_levels = 0.0
    last_candles = 0.0

    # Initial data
    if ensure_symbol(symbol):
        latest_state["tick"] = get_tick_data(symbol)
        latest_state["regime"] = get_regime_data(symbol)
        latest_state["account"] = get_account_data(symbol)
        latest_state["candles"] = get_candles_data(symbol)
        latest_state["levels"] = get_levels_data(symbol)

    while True:
        now = time.time()

        # Tick (high frequency)
        if ensure_symbol(symbol):
            tick = get_tick_data(symbol)
            if tick:
                latest_state["tick"] = tick
                await broadcast(tick)

        # Slow data: regime + multi-TF trends
        if now - last_slow >= SLOW_INTERVAL:
            regime = get_regime_data(symbol)
            if regime:
                latest_state["regime"] = regime
                await broadcast(regime)
            last_slow = now

        # Account info
        if now - last_account >= ACCOUNT_INTERVAL:
            acct = get_account_data(symbol)
            if acct:
                latest_state["account"] = acct
                await broadcast(acct)
            last_account = now

        # Candles
        if now - last_candles >= CANDLES_INTERVAL:
            candles = get_candles_data(symbol)
            if candles:
                latest_state["candles"] = candles
                await broadcast(candles)
            last_candles = now

        # Levels
        if now - last_levels >= LEVELS_INTERVAL:
            levels = get_levels_data(symbol)
            if levels:
                latest_state["levels"] = levels
                await broadcast(levels)
            last_levels = now

        await asyncio.sleep(UPDATE_INTERVAL)


# ── HTTP health endpoint ───────────────────────────────────────────

async def http_handler(reader, writer):
    """Simple HTTP handler for health check."""
    request = (await reader.read(1024)).decode("utf-8")
    if "GET /health" in request:
        response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
        response += json.dumps({
            "status": "ok",
            "clients": len(connected_clients),
            "symbol": symbol,
            "mt5_connected": mt5.terminal_info() is not None,
        })
    else:
        response = "HTTP/1.1 404 Not Found\r\n\r\n"
    writer.write(response.encode())
    await writer.drain()
    writer.close()


async def run_http_server(host, port):
    """Run a simple HTTP server for health checks."""
    server = await asyncio.start_server(http_handler, host, port + 1)
    print(f"Health HTTP server on {host}:{port + 1}")
    async with server:
        await server.serve_forever()


# ── Main ───────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="MT5 Bridge Service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="WebSocket port")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol to stream")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    global symbol
    symbol = args.symbol

    print("=" * 60)
    print("  MT5 Bridge Service")
    print("  Connects to MetaTrader 5 and exposes live data")
    print("=" * 60)
    print()

    if not mt5_init():
        print("FATAL: Could not initialize MT5")
        sys.exit(1)

    if not ensure_symbol(symbol):
        print(f"FATAL: Symbol {symbol} not available")
        mt5.shutdown()
        sys.exit(1)

    print(f"\nStreaming: {symbol}")
    print(f"WebSocket: ws://{args.host}:{args.port}")
    print(f"Health:    http://{args.host}:{args.port + 1}/health")
    print("Press Ctrl+C to stop.\n")

    # Start servers
    async with ws_serve(handle_client, args.host, args.port):
        print(f"WebSocket server started on port {args.port}")
        # Run data loop and HTTP server concurrently
        await asyncio.gather(
            data_loop(),
            run_http_server(args.host, args.port),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped.")
    finally:
        mt5.shutdown()
