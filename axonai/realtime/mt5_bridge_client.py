"""
MT5 Bridge Client — runs in WSL, connects to the Windows MT5 Bridge Service
and provides data to the dashboard as if it were the local daemon.

Usage:
    from axonai.realtime.mt5_bridge_client import BridgeClient
    client = BridgeClient(host="172.x.x.x", port=8765)
    client.start()

The bridge client connects to the Windows bridge WebSocket and relays
all data (tick, regime, account, candles, levels) to the dashboard's
broadcast() method.
"""

import asyncio
import json
import time
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class BridgeClient:
    """Connects to the Windows MT5 Bridge and relays data to the dashboard.

    The bridge runs on the Windows host and provides live MT5 data.
    This client connects to it and feeds data into the dashboard server's
    broadcast mechanism.
    """

    def __init__(self, host="127.0.0.1", port=8765, dashboard_server=None,
                 auto_reconnect=True, reconnect_delay=3.0,
                 on_connected=None, on_tick=None):
        self.host = host
        self.port = port
        self.dashboard = dashboard_server
        if dashboard_server:
            dashboard_server.bridge_client = self
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.on_connected = on_connected
        self.on_tick = on_tick
        self._running = False
        self._pending_historical = {}
        self._thread = None
        self._loop = None
        self._ws = None

    @property
    def url(self):
        return f"ws://{self.host}:{self.port}"

    def start(self):
        """Start the bridge client in a background thread."""
        if self._running:
            logger.warning("BridgeClient already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"BridgeClient started, connecting to {self.url}")

    def stop(self):
        """Stop the bridge client."""
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self):
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            logger.error(f"BridgeClient loop error: {e}")
        finally:
            self._loop.close()

    async def _connect_loop(self):
        """Continuously try to connect to the bridge."""
        import websockets
        while self._running:
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    logger.info(f"Connected to MT5 bridge at {self.url}")
                    # Fire on_connected callback if set
                    if self.on_connected:
                        try:
                            if asyncio.iscoroutinefunction(self.on_connected):
                                await self.on_connected(self)
                            else:
                                self.on_connected(self)
                        except Exception as e:
                            logger.error(f"on_connected callback error: {e}")
                    await self._handle_messages(ws)
            except (ConnectionRefusedError, OSError,
                    websockets.exceptions.WebSocketException) as e:
                if self._running:
                    logger.warning(f"Bridge connection failed: {e}, "
                                   f"retrying in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"Bridge client error: {e}")
                if self._running:
                    await asyncio.sleep(self.reconnect_delay)
            finally:
                self._ws = None

    async def _handle_messages(self, ws):
        """Receive and relay messages from the bridge."""
        async for message in ws:
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                # Relay to dashboard broadcast
                if self.dashboard and hasattr(self.dashboard, "broadcast"):
                    if msg_type != "tick":
                        self.dashboard.broadcast(data)

                # Handle "historical" responses
                if msg_type == "historical":
                    req_id = data.get("request_id")
                    if req_id in self._pending_historical:
                        fut = self._pending_historical.pop(req_id, None)
                        if fut:
                            fut.set_result(data.get("bars", []))
                        continue
                    
                    if self.dashboard:
                        bars = data.get("bars", [])
                        tf = data.get("timeframe", "M15")
                        if bars:
                            candles_msg = {
                                "type": "candles",
                                "timeframe": tf,
                                "candles": [
                                    {
                                        "time": b["time"],
                                        "open": b["open"],
                                        "high": b["high"],
                                        "low": b["low"],
                                        "close": b["close"],
                                    }
                                    for b in bars
                                ],
                            }
                            self.dashboard.broadcast(candles_msg)
                            logger.info(
                                "Bridge relayed historical %s: %d bars -> candles",
                                tf, len(bars),
                            )

                # Fire on_tick callback if registered
                if msg_type == "tick" and self.on_tick:
                    try:
                        self.on_tick(data)
                    except Exception as e:
                        logger.error(f"on_tick callback error: {e}")

                # Log data types for debugging
                if msg_type in ("tick", "regime", "account", "candles", "levels", "historical"):
                    logger.debug(f"Bridge relayed: {msg_type}")
                elif msg_type == "symbols_list":
                    logger.info(f"Bridge symbols: {len(data.get('symbols', []))} available")
                elif msg_type == "pong":
                    pass

            except json.JSONDecodeError:
                pass

    def send_message(self, data):
        """Send an arbitrary JSON message to the bridge."""
        if not self._loop or not self._running:
            logger.warning("Bridge not running, cannot send message")
            return False
        msg = json.dumps(data) if isinstance(data, dict) else data
        asyncio.run_coroutine_threadsafe(
            self._ws_send(msg), self._loop
        )
        return True

    def request_historical(self, symbol, timeframe, from_ts, to_ts, request_id=0):
        """Send a request for historical data through the bridge."""
        if not self._loop or not self._running:
            logger.warning("Bridge not running, cannot request historical data")
            return False
        msg = json.dumps({
            "type": "get_historical",
            "symbol": symbol,
            "timeframe": timeframe,
            "from": from_ts,
            "to": to_ts,
            "request_id": request_id,
        })
        asyncio.run_coroutine_threadsafe(
            self._ws_send(msg), self._loop
        )
        return True

    async def _ws_send(self, msg):
        """Send a message via WebSocket."""
        if self._ws:
            try:
                await self._ws.send(msg)
            except Exception as e:
                logger.error(f"Bridge send error: {e}")

    def is_connected(self):
        """Check if connected to the bridge."""
        return self._ws is not None and self._running

    @property
    def status(self):
        return {
            "connected": self.is_connected(),
            "host": self.host,
            "port": self.port,
            "url": self.url,
        }
