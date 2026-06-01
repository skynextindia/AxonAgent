"""FastAPI WebSocket server for real-time visual signaling dashboard.

Integrates with AxonDaemon, runs in a background thread, and streams
high-frequency market ticks, technical levels, and multi-agent
thinking outputs to client browsers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Dict, List, Set, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import uvicorn

logger = logging.getLogger(__name__)


def convert_numpy(obj: Any) -> Any:
    """Recursively convert numpy types to native Python types for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [convert_numpy(x) for x in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        return float(obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return convert_numpy(obj.tolist())
    return obj


class DashboardServer:
    """Manages the FastAPI lifecycle and WebSocket broadcasts."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.app = FastAPI(title="AxonAI Real-Time Signaling Dashboard")
        self.active_connections: Set[WebSocket] = set()
        self._lock = threading.Lock()

        self.daemon = None

        # In-memory history for hydrating newly connected clients instantly
        self.history: Dict[str, Any] = {
            "tick": None,
            "regime": None,
            "levels": None,
            "account": None,
            "news_data": None,
            "candles": {},       # Map of timeframe -> latest candle dict
            "events": [],        # List of last 30 detected events
            "agent_trace": [],   # List of last 50 agent steps
            "decision": None,    # Latest final decision
        }

        # Setup routing
        self._setup_routes()
        self._load_session()

    def _setup_routes(self):
        """Bind endpoints to FastAPI app."""
        
        @self.app.get("/status")
        def get_status():
            with self._lock:
                return {
                    "status": "healthy",
                    "connections": len(self.active_connections),
                    "uptime_seconds": (datetime.now() - self._start_time).total_seconds() if hasattr(self, "_start_time") else 0
                }

        @self.app.get("/config")
        def get_config():
            with self._lock:
                if self.daemon:
                    return {"status": "success", "config": self.daemon.config}
                return {"status": "success", "config": {
                    "tick_poll_interval_ms": 100,
                    "realtime_suppress_asian": True,
                    "realtime_level_reset_atr_multiple": 2.0,
                    "indicator_rsi_length": 14,
                    "indicator_ema_fast": 12,
                    "indicator_ema_slow": 26,
                }}

        @self.app.post("/config")
        def update_config(new_config: dict):
            with self._lock:
                if self.daemon:
                    # Update config in daemon and dependent modules!
                    self.daemon.config.update(new_config)
                    # Expose configuration update to event_detector, tick_engine, live_state, etc.
                    if hasattr(self.daemon, "tick_engine") and self.daemon.tick_engine:
                        self.daemon.tick_engine.poll_interval_ms = int(self.daemon.config.get("tick_poll_interval_ms", 100))
                    if hasattr(self.daemon, "event_detector") and self.daemon.event_detector:
                        self.daemon.event_detector._suppress_asian = self.daemon.config.get("realtime_suppress_asian", True)
                        self.daemon.event_detector._level_reset_atr_mult = float(self.daemon.config.get("realtime_level_reset_atr_multiple", 2.0))
                    if hasattr(self.daemon, "live_state") and self.daemon.live_state:
                        self.daemon.live_state.config.update(new_config)
                    if hasattr(self.daemon, "live_evidence") and self.daemon.live_evidence:
                        self.daemon.live_evidence.config.update(new_config)
                    return {"status": "success", "config": self.daemon.config}
                return {"status": "success", "config": {
                    "tick_poll_interval_ms": new_config.get("tick_poll_interval_ms", 100),
                    "realtime_suppress_asian": new_config.get("realtime_suppress_asian", True),
                    "realtime_level_reset_atr_multiple": new_config.get("realtime_level_reset_atr_multiple", 2.0),
                    "indicator_rsi_length": new_config.get("indicator_rsi_length", 14),
                    "indicator_ema_fast": new_config.get("indicator_ema_fast", 12),
                    "indicator_ema_slow": new_config.get("indicator_ema_slow", 26),
                }}

        @self.app.post("/trigger")
        def trigger_event():
            from axonai.realtime.event_types import MarketEvent, EventType, EventPriority
            from datetime import datetime
            with self._lock:
                if self.daemon:
                    price = self.daemon.live_state.current_price if hasattr(self.daemon.live_state, "current_price") else 1.0
                    event = MarketEvent(
                        event_type=EventType.LEVEL_BREACH,
                        priority=EventPriority.CRITICAL,
                        timestamp=datetime.now(),
                        symbol=self.daemon.yf_symbol,
                        price=price,
                        details={"news": "USER FORCED TEST EVENT"}
                    )
                    # Bypass cooldown so it fires immediately
                    self.daemon.event_detector._cooldown_until = datetime.min
                    self.daemon.event_detector.event_queue.put_nowait(event)
                    return {"status": "triggered", "event": str(event)}
                return {"status": "error", "message": "Daemon not registered"}
        @self.app.post("/api/emergency_stop")
        def emergency_stop():
            with self._lock:
                if self.daemon:
                    self.daemon.stop()
                    return {"status": "success", "message": "Daemon halted"}
                return {"status": "error", "message": "Daemon not registered"}

        @self.app.post("/api/close_all")
        def close_all_positions():
            with self._lock:
                if self.daemon and hasattr(self.daemon, "execution"):
                    self.daemon.execution.close_all()
                    return {"status": "success", "message": "Close all signal sent to MT5"}
                return {"status": "error", "message": "Execution engine not available"}

        @self.app.post("/api/pause_llm")
        def pause_llm():
            with self._lock:
                if self.daemon and hasattr(self.daemon, "llm"):
                    self.daemon.llm.paused = not getattr(self.daemon.llm, "paused", False)
                    state = "paused" if self.daemon.llm.paused else "resumed"
                    return {"status": "success", "message": f"LLM operations {state}"}
                return {"status": "error", "message": "LLM bridge not available"}

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            # Security: only accept WebSocket connections from localhost origins
            origin = websocket.headers.get("origin", "")
            allowed_origins = {"http://127.0.0.1:8000", "http://localhost:8000",
                               "http://127.0.0.1", "http://localhost",
                               "http://127.0.0.1:3000", "http://localhost:3000"}
            if origin and origin not in allowed_origins:
                await websocket.close(code=1008, reason="Origin not allowed")
                logger.warning("Dashboard WS: rejected connection from origin: %s", origin)
                return

            await websocket.accept()
            with self._lock:
                self.active_connections.add(websocket)
            logger.info("Dashboard WS: client connected. Total: %d", len(self.active_connections))
            
            # Hydrate client immediately with latest known state
            try:
                await self._hydrate_client(websocket)
            except Exception as e:
                logger.warning("Dashboard WS: failed to hydrate client: %s", e)

            try:
                while True:
                    try:
                        data = await websocket.receive_json()
                        if isinstance(data, dict):
                            msg_type = data.get("type")
                            if msg_type == "ping":
                                await websocket.send_json({
                                    "type": "pong",
                                    "timestamp": data.get("timestamp")
                                })
                            elif msg_type == "switch_pair":
                                pair = data.get("pair")
                                mt5_symbol = data.get("mt5")
                                is_active = False
                                with self._lock:
                                    if self.daemon:
                                        d_clean = self.daemon.mt5_symbol.replace("=X", "").replace("=x", "").upper()
                                        p_clean = mt5_symbol.replace("=X", "").replace("=x", "").upper() if mt5_symbol else ""
                                        yf_clean = self.daemon.yf_symbol.replace("=X", "").replace("=x", "").upper()
                                        if d_clean in p_clean or p_clean in d_clean or yf_clean in p_clean or p_clean in yf_clean:
                                            is_active = True
                                    elif self.bridge_client and self.bridge_client.is_connected():
                                        # Bridge mode — forward switch to the MT5 bridge
                                        is_active = True
                                        self.bridge_client.send_message({
                                            "type": "switch_pair",
                                            "symbol": mt5_symbol or pair,
                                            "mt5": mt5_symbol or pair,
                                        })
                                        logger.info(f"Bridge: forwarded switch_pair to {mt5_symbol or pair}")
                                
                                if is_active:
                                    logger.info("Dashboard WS: client switched back to active symbol, re-hydrating")
                                    await self._hydrate_client(websocket)
                                else:
                                    daemon_sym = self.daemon.yf_symbol if self.daemon else "EURUSD=X"
                                    await websocket.send_json({
                                        "type": "agent",
                                        "agent_name": "SYSTEM",
                                        "status": "active",
                                        "message": f"[WARNING] The trading daemon is currently locked to {daemon_sym}. Live telemetry and cognitive execution are active for that pair only. To monitor or trade {pair}, restart the daemon using: python cli/main.py live --ticker \"{pair}\"",
                                        "tool_calls": [],
                                        "timestamp": datetime.now().strftime("%H:%M:%S")
                                    })
                    except Exception:
                        try:
                            # Clear buffer if raw string is sent instead
                            await websocket.receive_text()
                        except Exception:
                            raise
            except WebSocketDisconnect:
                with self._lock:
                    self.active_connections.discard(websocket)
                logger.info("Dashboard WS: client disconnected. Total: %d", len(self.active_connections))
            except Exception as e:
                with self._lock:
                    self.active_connections.discard(websocket)
                logger.debug("Dashboard WS: connection error: %s", e)

    async def _hydrate_client(self, websocket: WebSocket):
        """Send all cached state history to a newly connected client."""
        # 1. Thread-safely update and copy dynamic state cache
        with self._lock:
            # Update candles dynamically from the active daemon if registered
            if hasattr(self, "daemon") and self.daemon:
                for tf in ["M15", "H1", "H4"]:
                    try:
                        self.history["candles"][tf] = self.daemon._get_candles_payload(tf)
                    except Exception as e:
                        logger.warning("Dashboard WS: failed to update active candle for %s: %s", tf, e)

            # Copy cached states to local variables under lock
            account_data = self.history["account"]
            tick_data = self.history["tick"]
            regime_data = self.history["regime"]
            levels_data = self.history["levels"]
            candles_data = list(self.history["candles"].items())
            events_data = list(self.history["events"])
            trace_data = list(self.history["agent_trace"])
            decision_data = self.history["decision"]
            news_data = self.history["news_data"]

        # 2. Perform all asynchronous sends safely OUTSIDE the lock block!
        # 1. Account details
        if account_data:
            await websocket.send_json(account_data)
        # 2. Latest tick
        if tick_data:
            await websocket.send_json(tick_data)
        # 3. Market Regime
        if regime_data:
            await websocket.send_json(regime_data)
        # 4. Technical Levels
        if levels_data:
            await websocket.send_json(levels_data)
        # 5. Candles
        for tf, candle_data in candles_data:
            await websocket.send_json(candle_data)
        # 6. Event history
        for event in events_data:
            await websocket.send_json({**event, "historical": True})
        # 7. Agent dynamic log trace
        for log_entry in trace_data:
            await websocket.send_json({**log_entry, "historical": True})
        # 8. Latest final trade decision
        if decision_data:
            await websocket.send_json(decision_data)
        # 9. Sentiment News Feed
        if news_data:
            await websocket.send_json(news_data)

    def broadcast(self, message: Dict[str, Any]):
        """Thread-safe queueing of message broadcast across all websockets."""
        message = convert_numpy(message)
        msg_type = message.get("type")
        if not msg_type:
            return

        with self._lock:
            # Update cache history
            save_needed = False
            if msg_type in ["tick", "regime", "levels", "account", "decision", "news_data"]:
                self.history[msg_type] = message
                if msg_type in ["decision", "news_data"]:
                    save_needed = True
            elif msg_type in ["candle", "candles"]:
                tf = message.get("timeframe")
                if tf:
                    self.history["candles"][tf] = message
            elif msg_type == "event":
                self.history["events"].append(message)
                if len(self.history["events"]) > 30:
                    self.history["events"].pop(0)
                save_needed = True
            elif msg_type == "agent":
                self.history["agent_trace"].append(message)
                if len(self.history["agent_trace"]) > 50:
                    self.history["agent_trace"].pop(0)
                save_needed = True

            if save_needed:
                self._save_session()

            if not self.active_connections:
                return

        # Uvicorn and FastAPI run inside an asyncio event loop.
        # Since daemon operates in a regular thread, we bridge the call to the loop.
        if hasattr(self, "_loop") and self._loop:
            asyncio.run_coroutine_threadsafe(self._async_broadcast(message), self._loop)

    async def _async_broadcast(self, message: Dict[str, Any]):
        """Asynchronously send message to all sockets."""
        with self._lock:
            targets = list(self.active_connections)
        
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                with self._lock:
                    self.active_connections.discard(ws)

    def _save_session(self):
        """Save event history, agent traces, latest decision, and levels state to disk."""
        import json
        try:
            levels_state = []
            if self.daemon and hasattr(self.daemon, "live_evidence") and self.daemon.live_evidence:
                levels_state = [{
                    "price": float(l.price),
                    "level_type": l.level_type,
                    "touches": int(l.touches),
                    "strength": float(l.strength),
                    "is_active": bool(l.is_active),
                    "last_touch": l.last_touch.isoformat() if isinstance(l.last_touch, datetime) else str(l.last_touch)
                } for l in self.daemon.live_evidence.price_levels]

            state = {
                "events": self.history["events"],
                "agent_trace": self.history["agent_trace"],
                "decision": self.history["decision"],
                "news_data": self.history["news_data"],
                "levels_state": levels_state
            }
            with open(".axon_session.json", "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning("Dashboard API: failed to save session: %s", e)

    def _load_session(self):
        """Load session state from disk on startup."""
        import json
        import os
        if os.path.exists(".axon_session.json"):
            try:
                with open(".axon_session.json", "r") as f:
                    state = json.load(f)
                with self._lock:
                    self.history["events"] = state.get("events", [])
                    self.history["agent_trace"] = state.get("agent_trace", [])
                    self.history["decision"] = state.get("decision", None)
                    self.history["news_data"] = state.get("news_data", None)
                    self.history["levels_state"] = state.get("levels_state", [])
                logger.info("Dashboard API: restored %d events, %d agent traces, %d S/R levels from local storage",
                            len(self.history["events"]), len(self.history["agent_trace"]), len(self.history.get("levels_state", [])))
            except Exception as e:
                logger.warning("Dashboard API: failed to load session: %s", e)

    def start_in_background(self):
        """Launch the API and web server in a daemon thread."""
        self._start_time = datetime.now()
        
        # Mount the static directory statically (creates automatically if needed)
        import os
        static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cli", "static")
        os.makedirs(static_dir, exist_ok=True)
        self.app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

        server_thread = threading.Thread(target=self._run_server, daemon=True, name="DashboardServer")
        server_thread.start()
        logger.info("Dashboard API Server starting on thread %s", server_thread.name)

        # Start background news poller thread!
        news_thread = threading.Thread(target=self._news_poller, daemon=True, name="DashboardNewsPoller")
        news_thread.start()

    def _news_poller(self):
        """Background thread: periodically polls news sources (non-blocking) and backfills."""
        import time
        from datetime import datetime
        from axonai.dataflows.yfinance_news import get_global_news_yfinance
        from axonai.dataflows.forex_social import fetch_forex_social_feed
        from axonai.dataflows.reddit import fetch_reddit_posts

        logger.info("Dashboard API: Background News Sentiment poller started.")
        while not hasattr(self, "_poller_stop") or not self._poller_stop.is_set():
            try:
                # 1. Determine active ticker (default to EURUSD)
                ticker = "EURUSD=X"
                if self.daemon and hasattr(self.daemon, "yf_symbol"):
                    ticker = self.daemon.yf_symbol

                curr_date = datetime.now().strftime("%Y-%m-%d")
                
                # 2. Fetch in non-blocking background mode
                logger.info("Dashboard API: Refreshing News Sentiment Feed (cached continuous mode)...")
                
                # Fetch global news
                news = get_global_news_yfinance(curr_date=curr_date, look_back_days=3, limit=10)
                
                # Fetch social feed (MT5 ticker mapping e.g. EURUSDm -> EURUSD)
                fs_ticker = ticker.replace("=X", "").replace("=x", "")
                forex_social = fetch_forex_social_feed(fs_ticker, limit=10)
                
                # Fetch reddit
                reddit = fetch_reddit_posts(fs_ticker)
                
                # 3. Update cache history and broadcast
                payload = {
                    "type": "news_data",
                    "news": news,
                    "forex_social": forex_social,
                    "reddit": reddit,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                }
                
                self.broadcast(payload)
                logger.info("Dashboard API: News Sentiment Feed cache refreshed successfully.")
                
            except Exception as e:
                logger.warning("Dashboard API: Background News Sentiment poll failed (offline or connection issue): %s", e)
            
            # Wait for 300 seconds (5 minutes) before next update
            time.sleep(300.0)

    def _run_server(self):
        """Target for Uvicorn runner inside the thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        config = uvicorn.Config(
            self.app, 
            host=self.host, 
            port=self.port, 
            log_level="warning", 
            loop="asyncio"
        )
        server = uvicorn.Server(config)
        self._loop.run_until_complete(server.serve())


# Global server instance placeholder
_server_instance: Optional[DashboardServer] = None


def start_dashboard(host: str = "127.0.0.1", port: int = 8000) -> DashboardServer:
    """Helper to initialize and start the global dashboard server."""
    global _server_instance
    if _server_instance is None:
        _server_instance = DashboardServer(host, port)
        _server_instance.start_in_background()
    return _server_instance


def get_dashboard() -> Optional[DashboardServer]:
    """Get the active global dashboard server instance."""
    return _server_instance
