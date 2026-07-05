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
from axonai.default_config import DEFAULT_CONFIG

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
    elif isinstance(obj, datetime):
        return obj.isoformat()
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
        self.fallback_config = DEFAULT_CONFIG.copy()

        # CHANGE 10A: Broadcast throttle
        self._last_broadcast_ms: float = 0.0
        self._broadcast_interval_ms: float = DEFAULT_CONFIG.get(
            "dashboard_broadcast_interval_ms", 125.0
        )

        # In-memory history for hydrating newly connected clients instantly
        self.history: Dict[str, Any] = {
            "tick": None,
            "regime": None,
            "levels": None,
            "account": None,
            "calendar_data": None,   # Latest economic calendar broadcast
            "candles": {},           # Map of timeframe -> latest candle dict
            "events": [],            # List of last 30 detected events
            "decision": None,        # Latest final trade decision
            "trigger_metrics": None, # Real-time entry trigger conditions
            "mode": None,            # Execution mode badge (paper / live)
            "trade_state": None,     # Current phase, health, MFE/MAE
            "location_context": None,# Distance to levels, at_structure
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
                return {"status": "success", "config": self.fallback_config}

        @self.app.post("/config")
        def update_config(new_config: dict):
            with self._lock:
                if self.daemon:
                    # Update config in daemon and dependent modules!
                    self.daemon.config.update(new_config)
                    # Expose configuration update to tick_engine, live_state, etc.
                    if hasattr(self.daemon, "tick_engine") and self.daemon.tick_engine:
                        self.daemon.tick_engine.poll_interval_ms = int(self.daemon.config.get("tick_poll_interval_ms", 100))
                    if hasattr(self.daemon, "reversal_model") and self.daemon.reversal_model:
                        self.daemon.reversal_model.config.update(new_config)
                    if hasattr(self.daemon, "live_state") and self.daemon.live_state:
                        self.daemon.live_state.config.update(new_config)
                    if hasattr(self.daemon, "live_evidence") and self.daemon.live_evidence:
                        self.daemon.live_evidence.config.update(new_config)
                    return {"status": "success", "config": self.daemon.config}
                
                self.fallback_config.update(new_config)
                return {"status": "success", "config": self.fallback_config}

        @self.app.post("/trigger")
        def trigger_event(event_type: str = "level_breach", peak_type: str = "microstructure_exhaustion"):
            from axonai.realtime.event_types import MarketEvent, EventType, EventPriority
            from datetime import datetime
            with self._lock:
                if self.daemon:
                    price = self.daemon.live_state.current_price if hasattr(self.daemon.live_state, "current_price") else 1.0
                    try:
                        ev_type = EventType(event_type.lower())
                    except ValueError:
                        ev_type = EventType.LEVEL_BREACH
                    
                    details = {"news": "USER FORCED TEST EVENT"}
                    if ev_type == EventType.PEAK_DETECTION:
                        details = {
                            "peak_type": peak_type,
                            "direction": "bearish_reversal",
                            "peak_price": price,
                            "intensity": "HIGH",
                            "velocity_divergence": 10.0,
                            "price_per_tick_efficiency": 0.05,
                            "divergence_warning": True,
                            "peak_confirmed": True,
                            "peak_confidence": 0.85
                        }
                    
                    # Trigger endpoint currently unsupported without EventDetector
                    return {"status": "error", "message": "Trigger via API disabled on pure-math engine"}
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
                if self.daemon and hasattr(self.daemon, "_close_all_positions"):
                    n = self.daemon._close_all_positions("Manual close-all (dashboard)")
                    return {"status": "success", "message": f"Closed {n} position(s)"}
                return {"status": "error", "message": "Execution engine not available"}

        @self.app.post("/api/pause_trading")
        def pause_trading():
            with self._lock:
                if self.daemon and hasattr(self.daemon, "paused"):
                    self.daemon.paused = not self.daemon.paused
                    state = "paused" if self.daemon.paused else "resumed"
                    return {"status": "success", "message": f"Trading operations {state}", "paused": self.daemon.paused}
                return {"status": "error", "message": "Daemon not registered or not pausable"}

        @self.app.post("/api/pause_llm")
        def pause_llm():
            # Alias for backward compatibility
            return pause_trading()


        # /api/logs/decisions removed — pure-math engine has no LLM decision journal;
        # trade history is served exclusively by /api/logs/trades.

        @self.app.get("/api/logs/trades")
        def get_trades_log():
            import os
            import json
            import time
            from datetime import datetime, timedelta
            import concurrent.futures

            trades_path = os.path.join("reports", "signals.jsonl")
            if not os.path.exists(trades_path):
                return {"status": "success", "entries": []}
            
            # Blacklist of manually operated trades
            BLACKLIST_TIMESTAMPS = {
                "2026-06-03 16:56:58",
                "2026-06-03 16:50:37",
                "2026-06-03 16:50:31",
                "2026-06-03 16:43:54",
                "2026-06-02 17:47:22",
                "2026-05-28 00:23:25"
            }
            BLACKLIST_TICKETS = {
                4152710779,
                4152685271,
                4152655084,
                4127567959,
                2001608993
            }

            def parse_time_str(ts_str):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return datetime.strptime(ts_str, fmt)
                    except ValueError:
                        continue
                raise ValueError(f"Unknown time format: {ts_str}")

            def format_duration(start_str, end_str):
                try:
                    t1 = parse_time_str(start_str)
                    t2 = parse_time_str(end_str)
                    diff = t2 - t1
                    secs = int(diff.total_seconds())
                    if secs < 0:
                        return "--"
                    days, rem = divmod(secs, 86400)
                    hours, rem = divmod(rem, 3600)
                    minutes, seconds = divmod(rem, 60)
                    if days > 0:
                        return f"{days}d {hours}h {minutes}m"
                    elif hours > 0:
                        return f"{hours}h {minutes}m"
                    elif minutes > 0:
                        return f"{minutes}m {seconds}s"
                    else:
                        return f"{seconds}s"
                except Exception:
                    return "--"

            def get_session_from_time(dt):
                # Classify session based on UTC hour
                import time
                is_dst = time.daylight and time.localtime().tm_isdst > 0
                utc_offset = - (time.altzone if is_dst else time.timezone) / 3600.0
                utc_dt = dt - timedelta(hours=utc_offset)
                hour = utc_dt.hour + utc_dt.minute / 60.0
                
                if 12.0 <= hour < 16.0:
                    return "Overlap"
                elif 7.0 <= hour < 12.0:
                    return "London"
                elif 16.0 <= hour < 21.0:
                    return "New York"
                elif 21.0 <= hour or hour < 7.0:
                    return "Sydney/Tokyo"
                else:
                    return "Rollover"

            def compute_drawdown_peak(symbol, direction, entry_price, exit_price, entry_dt, exit_dt, outcome, reason, entry_signal=None):
                is_jpy = "JPY" in symbol.upper()
                pip_multiplier = 0.01 if is_jpy else 0.0001
                
                if direction.upper() == "BUY":
                    net_pips = (exit_price - entry_price) / pip_multiplier
                else:
                    net_pips = (entry_price - exit_price) / pip_multiplier
                    
                bars = []
                # 1. Direct MT5 mode (if running on Windows with local MT5 package)
                try:
                    import MetaTrader5 as mt5
                    if mt5.terminal_info() is not None:
                        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, entry_dt, exit_dt)
                        if rates is not None:
                            bars = [{"high": float(r["high"]), "low": float(r["low"])} for r in rates]
                except Exception as e:
                    logger.warning("DashboardServer: Failed to fetch MT5 historical rates: %s", e)
                    
                # 2. Bridge Client mode
                if not bars:
                    try:
                        # self is DashboardServer
                        client = getattr(self, "bridge_client", None)
                        if client and client.is_connected():
                            from_ts = int(entry_dt.timestamp())
                            to_ts = int(exit_dt.timestamp())
                            request_id = f"trades_{int(time.time() * 1000)}"
                            fut = concurrent.futures.Future()
                            client._pending_historical[request_id] = fut
                            client.request_historical(symbol, "M1", from_ts, to_ts, request_id=request_id)
                            try:
                                bars = fut.result(timeout=1.5)
                            except Exception as e:
                                logger.warning("DashboardServer: Bridge historical data timeout: %s", e)
                    except Exception as e:
                        logger.warning("DashboardServer: Bridge historical request failed: %s", e)
                        
                if bars:
                    try:
                        highs = [float(b.get("high", b.get("open", 0))) for b in bars]
                        lows = [float(b.get("low", b.get("open", 0))) for b in bars]
                        if highs and lows:
                            max_high = max(highs)
                            min_low = min(lows)
                            
                            if direction.upper() == "BUY":
                                drawdown_pips = (entry_price - min_low) / pip_multiplier
                                peak_pips = (max_high - entry_price) / pip_multiplier
                            else:
                                drawdown_pips = (max_high - entry_price) / pip_multiplier
                                peak_pips = (entry_price - min_low) / pip_multiplier
                                
                            return round(max(0.0, drawdown_pips), 1), round(max(0.0, peak_pips), 1)
                    except Exception as e:
                        logger.warning("DashboardServer: Failed to calculate drawdown/peak: %s", e)
                        
                # Fallback to estimate
                drawdown_pips = 0.0
                peak_pips = 0.0
                sl = None
                if entry_signal and isinstance(entry_signal.get("trade_result"), dict):
                    sl = entry_signal["trade_result"].get("sl")
                    
                if outcome == "WIN":
                    peak_pips = abs(net_pips)
                    drawdown_pips = 0.0
                elif outcome == "LOSS":
                    if sl and sl > 0:
                        if direction.upper() == "BUY":
                            drawdown_pips = (entry_price - sl) / pip_multiplier
                        else:
                            drawdown_pips = (sl - entry_price) / pip_multiplier
                    else:
                        drawdown_pips = abs(net_pips)
                    peak_pips = 0.0
                return round(max(0.0, drawdown_pips), 1), round(max(0.0, peak_pips), 1)

            try:
                opens = {}
                closes = {}

                with open(trades_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            timestamp = entry.get("timestamp")
                            if timestamp in BLACKLIST_TIMESTAMPS:
                                continue

                            if entry.get("type") == "trade_closed":
                                ticket = entry.get("ticket")
                                if ticket and int(ticket) in BLACKLIST_TICKETS:
                                    continue
                                if ticket:
                                    closes[int(ticket)] = entry
                            else:
                                result = entry.get("trade_result")
                                if isinstance(result, dict):
                                    ticket = result.get("order")
                                    if ticket and int(ticket) in BLACKLIST_TICKETS:
                                        continue
                                    is_executed = result.get("retcode") == 10009 or result.get("order", 0) > 0
                                    if is_executed and ticket:
                                        opens[int(ticket)] = entry
                        except Exception:
                            continue

                merged_entries = []
                all_tickets = set(opens.keys()) | set(closes.keys())

                for ticket in all_tickets:
                    open_evt = opens.get(ticket)
                    close_evt = closes.get(ticket)

                    base = {}
                    if open_evt:
                        base.update(open_evt)
                    if close_evt:
                        base.update(close_evt)

                    # Safe access to trade_result fields
                    trade_result = open_evt.get("trade_result") if open_evt and isinstance(open_evt.get("trade_result"), dict) else None

                    trade = {
                        "ticket": ticket,
                        "symbol": base.get("symbol") or base.get("mt5_symbol") or "EURUSD",
                        "system": base.get("system") or (open_evt and open_evt.get("system")) or (close_evt and close_evt.get("system")) or "optimized",
                        "direction": base.get("direction") or (open_evt and open_evt.get("decision", "").upper()) or "BUY",
                        "volume": base.get("volume") or (trade_result and trade_result.get("volume")),
                        "entry_price": base.get("entry_price") or (trade_result and trade_result.get("price")),
                        "exit_price": base.get("exit_price"),
                        "profit": base.get("profit"),
                        "pips": base.get("pips"),
                        "reason": base.get("reason") or "Open Position",
                        "exit_strategy": close_evt.get("exit_strategy") if close_evt else (base.get("exit_strategy") or "manual"),
                        "exit_urgency": close_evt.get("exit_urgency", 0.0) if close_evt else (base.get("exit_urgency") or 0.0),
                        "velocity_trailing_events": close_evt.get("velocity_trailing_events", []) if close_evt else base.get("velocity_trailing_events", []),
                        "outcome": base.get("outcome") or "OPEN",
                        "event_type": open_evt.get("event_type") if open_evt else base.get("event_type", "level_breach"),
                        "event_priority": open_evt.get("event_priority") if open_evt else base.get("event_priority", "INFO"),
                        "event_details": (open_evt.get("event_details") if open_evt else None) or (close_evt.get("event_details") if close_evt else None) or base.get("event_details", {}),
                        "dominant_regime": (open_evt.get("dominant_regime") if open_evt else None) or (close_evt.get("dominant_regime") if close_evt else None) or base.get("dominant_regime", "ranging"),
                        "regime_confidence": (open_evt.get("regime_confidence") if open_evt else None) or (close_evt.get("regime_confidence") if close_evt else None) or base.get("regime_confidence", 0.5),
                        "volatility": (open_evt.get("volatility") if open_evt else None) or (close_evt.get("volatility") if close_evt else None) or base.get("volatility", "medium"),
                        "spread_pips": (open_evt.get("spread_pips") if open_evt else None) or (close_evt.get("spread_pips") if close_evt else None) or base.get("spread_pips", 0.0),
                    }

                    entry_time_str = (
                        (open_evt.get("timestamp") if open_evt else None)
                        or (close_evt.get("entry_time") if close_evt else None)
                        or (close_evt.get("timestamp") if close_evt else None)
                    )
                    exit_time_str = close_evt.get("timestamp") if close_evt else None

                    trade["entry_time"] = entry_time_str
                    trade["exit_time"] = exit_time_str

                    entry_dt = None
                    exit_dt = None
                    if entry_time_str:
                        try:
                            entry_dt = parse_time_str(entry_time_str)
                        except Exception:
                            pass
                    if exit_time_str:
                        try:
                            exit_dt = parse_time_str(exit_time_str)
                        except Exception:
                            pass

                    if entry_time_str and exit_time_str:
                        trade["duration_str"] = format_duration(entry_time_str, exit_time_str)
                    else:
                        trade["duration_str"] = "--"

                    if entry_dt:
                        trade["session"] = get_session_from_time(entry_dt)
                    else:
                        trade["session"] = "--"

                    if trade["entry_price"] is not None and trade["exit_price"] is not None and entry_dt and exit_dt:
                        drawdown, peak = compute_drawdown_peak(
                            symbol=trade["symbol"],
                            direction=trade["direction"],
                            entry_price=float(trade["entry_price"]),
                            exit_price=float(trade["exit_price"]),
                            entry_dt=entry_dt,
                            exit_dt=exit_dt,
                            outcome=trade["outcome"],
                            reason=trade["reason"],
                            entry_signal=open_evt
                        )
                        trade["drawdown"] = drawdown
                        trade["peak"] = peak
                    else:
                        trade["drawdown"] = 0.0
                        trade["peak"] = 0.0

                    merged_entries.append(trade)

                merged_entries.sort(key=lambda x: x.get("entry_time") or "", reverse=True)
                return {"status": "success", "entries": merged_entries}
            except Exception as e:
                import traceback
                logging.getLogger(__name__).error("Error loading trades log: %s\n%s", e, traceback.format_exc())
                return {"status": "error", "message": str(e)}

        # /api/logs/dryrun removed — dry_run_session.jsonl belongs to the LLM dryrun
        # loop which is no longer part of the pure-math engine.

        @self.app.get("/api/logs/system")
        def get_system_log():
            import os
            log_path = os.path.join(os.path.expanduser("~"), ".axonai", "logs", "axon.log")
            if not os.path.exists(log_path):
                return {"status": "success", "lines": ["System log does not exist yet."]}
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                return {"status": "success", "lines": [line.strip() for line in lines[-200:]]}
            except Exception as e:
                return {"status": "error", "message": str(e)}

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
            logger.info("Hydrating client: account_data=%s", account_data if account_data else "None")
            tick_data = self.history["tick"]
            regime_data = self.history["regime"]
            levels_data = self.history["levels"]
            candles_data = list(self.history["candles"].items())
            events_data = list(self.history["events"])
            decision_data = self.history["decision"]
            calendar_data = self.history["calendar_data"]
            mode_data = self.history["mode"]
            trigger_metrics_data = self.history.get("trigger_metrics")
            # Lifecycle fields
            trade_state_data = self.history.get("trade_state")
            location_context_data = self.history.get("location_context")

        # 2. Perform all asynchronous sends safely OUTSIDE the lock block!
        # 0. Execution mode badge (send first so the header reflects mode immediately)
        if mode_data:
            await websocket.send_json(mode_data)
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
        # 7. Latest final trade decision
        if decision_data:
            await websocket.send_json(decision_data)
        # 8. Trigger Metrics (real-time entry conditions)
        if trigger_metrics_data:
            await websocket.send_json(trigger_metrics_data)
        # 9. Trade State (phase, health, MFE/MAE)
        if trade_state_data:
            await websocket.send_json(trade_state_data)
        # 10. Location Context (distance to levels, at_structure)
        if location_context_data:
            await websocket.send_json(location_context_data)
        # 11. Economic Calendar
        if calendar_data:
            await websocket.send_json(calendar_data)

    def broadcast(self, message: Dict[str, Any]):
        """Thread-safe queueing of message broadcast across all websockets."""
        message = convert_numpy(message)
        msg_type = message.get("type")
        if not msg_type:
            return

        # CHANGE 10A: Broadcast throttle gate — only throttle tick messages
        import time

        if msg_type == "tick":
            now_ms = time.perf_counter() * 1000
            if now_ms - self._last_broadcast_ms < self._broadcast_interval_ms:
                return  # drop tick, trading logic unaffected
            self._last_broadcast_ms = now_ms

        with self._lock:
            # Update cache history
            save_needed = False
            # Broadcast cache update: batch-update scalar fields directly
            if msg_type in [
                "tick",
                "regime",
                "levels",
                "account",
                "decision",
                "mode",
                "trigger_metrics",
                "trade_state",
                "location_context",
            ]:
                self.history[msg_type] = message
                if msg_type == "decision":
                    save_needed = True
            elif msg_type == "news_data":
                # Calendar-only payload — store under the correct key
                self.history["calendar_data"] = message
            elif msg_type in ["candle", "candles"]:
                tf = message.get("timeframe")
                if tf:
                    self.history["candles"][tf] = message
            elif msg_type == "event":
                self.history["events"].append(message)
                if len(self.history["events"]) > 30:
                    self.history["events"].pop(0)
                save_needed = True

            if save_needed:
                self._save_session()

            if not self.active_connections:
                if msg_type == "account":
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning("broadcast(%s): no active WebSocket connections! Loop=%s", msg_type, bool(self._loop))
                return

        # Uvicorn and FastAPI run inside an asyncio event loop.
        # Since daemon operates in a regular thread, we bridge the call to the loop.
        if hasattr(self, "_loop") and self._loop:
            asyncio.run_coroutine_threadsafe(self._async_broadcast(message), self._loop)
        elif msg_type == "account":
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("broadcast(%s): event loop not available!", msg_type)

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
        """Save event history, latest decision, and levels state to disk."""
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
                "decision": self.history["decision"],
                "levels_state": levels_state,
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
                    self.history["decision"] = state.get("decision", None)
                    self.history["levels_state"] = state.get("levels_state", [])
                logger.info("Dashboard API: restored %d events, %d S/R levels from local storage",
                            len(self.history["events"]), len(self.history.get("levels_state", [])))
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

        # Start economic calendar poller thread
        news_thread = threading.Thread(target=self._calendar_poller, daemon=True, name="DashboardCalendarPoller")
        news_thread.start()

    def _calendar_poller(self):
        """Background thread: periodically fetches the economic calendar from NewsGuard.

        Replaced the old news/social poller. The pure-math engine has no use for
        forex social feeds or LLM-facing news. Only the economic calendar is retained
        because NewsGuard uses it to gate entries around high-impact events.
        """
        import time
        from datetime import datetime, timezone

        logger.info("Dashboard API: Economic calendar poller started.")
        while not hasattr(self, "_poller_stop") or not self._poller_stop.is_set():
            try:
                # Resolve the NewsGuard instance (daemon-registered or local fallback)
                ng = None
                if self.daemon and hasattr(self.daemon, "news_guard"):
                    ng = self.daemon.news_guard
                else:
                    if not hasattr(self, "_local_news_guard"):
                        from axonai.realtime.news_guard import NewsGuard
                        config = self.daemon.config if self.daemon else self.fallback_config
                        self._local_news_guard = NewsGuard(config)
                    ng = self._local_news_guard

                calendar_events = []
                if ng:
                    try:
                        ng.refresh()  # Respects internal 6-hour throttle; no-ops if fresh
                    except Exception:
                        pass
                    now_utc = datetime.now(timezone.utc)
                    for ev in getattr(ng, "_events", []):
                        dt = ev["dt"]
                        mins_away = (dt - now_utc).total_seconds() / 60.0
                        # Include events within ±12h lookback and +24h lookahead
                        if -720 <= mins_away <= 1440:
                            blocked, _ = ng.should_block_entry(
                                getattr(self.daemon, "mt5_symbol", "EURUSD") if self.daemon else "EURUSD",
                                now_utc,
                            )
                            calendar_events.append({
                                "title": ev["title"],
                                "currency": ev["currency"],
                                "impact": ev["impact"],
                                "time": dt.strftime("%H:%M UTC"),
                                "forecast": ev.get("forecast", ""),
                                "previous": ev.get("previous", ""),
                                "actual": ev.get("actual", ""),
                                "mins_away": round(mins_away, 0),
                                "is_blocking": abs(mins_away) <= 30 and blocked,
                            })
                    calendar_events.sort(key=lambda x: x["mins_away"])

                payload = {
                    "type": "news_data",
                    "calendar": calendar_events,
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                }
                self.broadcast(payload)
                logger.info(
                    "Dashboard API: Economic calendar broadcast — %d events.", len(calendar_events)
                )

            except Exception as e:
                logger.warning(
                    "Dashboard API: Calendar poll failed: %s", e
                )

            # Poll every 5 minutes (calendar events don't change faster than that)
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
