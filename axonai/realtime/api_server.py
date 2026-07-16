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

    # Default empty history template for per-symbol caches
    @staticmethod
    def _new_symbol_history():
        return {
            "tick": None,
            "regime": None,
            "levels": None,
            "candles": {},
            "events": [],
            "decision": None,
            "trigger_metrics": None,
            "mode": None,
            "trade_state": None,
            "location_context": None,
            "risk_state": None,
        }

    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.app = FastAPI(title="AxonAI Real-Time Signaling Dashboard")
        self.active_connections: Set[WebSocket] = set()
        self.client_subscriptions: Dict[WebSocket, str] = {}
        self._lock = threading.RLock()
        self._symbol_locks: Dict[str, threading.Lock] = {}

        # Multicurrency: dict of symbol -> AxonDaemon
        self.daemons: Dict[str, Any] = {}
        # Legacy single-daemon reference (for backward compatibility)
        self.daemon = None
        self.fallback_config = DEFAULT_CONFIG.copy()

        # Active symbols list (populated by register_daemon)
        self.active_symbols: list = []

        # CHANGE 10A: Broadcast throttle
        self._last_broadcast_ms: float = 0.0
        self._broadcast_interval_ms: float = DEFAULT_CONFIG.get(
            "dashboard_broadcast_interval_ms", 32.0
        )

        # Per-symbol history caches for hydrating newly connected clients instantly
        self.symbol_history: Dict[str, Dict[str, Any]] = {}
        
        # Per-symbol positions and orders caches to construct merged account state
        self.symbol_positions: Dict[str, list] = {}
        self.symbol_orders: Dict[str, list] = {}

        # Global (non-symbol-specific) history
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

    def _get_symbol_lock(self, symbol: str) -> threading.Lock:
        """Retrieve or create a thread lock specific to a symbol to reduce contention."""
        if not symbol:
            return self._lock
        sym_clean = symbol.replace("=X", "").replace("=x", "").upper()
        with self._lock:
            if sym_clean not in self._symbol_locks:
                self._symbol_locks[sym_clean] = threading.Lock()
            return self._symbol_locks[sym_clean]

    def register_daemon(self, symbol: str, daemon: Any):
        """Register a daemon instance for multicurrency support."""
        with self._lock:
            self.daemons[symbol] = daemon
            if symbol not in self.active_symbols:
                self.active_symbols.append(symbol)
            if symbol not in self.symbol_history:
                self.symbol_history[symbol] = self._new_symbol_history()
            
            # Keep legacy self.daemon pointing to the first one for backwards compatibility
            if self.daemon is None:
                self.daemon = daemon

        # Broadcast the updated active symbols list to all active connections
        self.broadcast({
            "type": "active_symbols",
            "symbols": self.active_symbols
        })

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
        def get_config(symbol: str = None):
            with self._lock:
                target_daemon = None
                if symbol:
                    # Clean symbol (e.g. EURUSD=X to EURUSD)
                    s_clean = symbol.replace("=X", "").replace("=x", "").upper()
                    for k, d in self.daemons.items():
                        if s_clean in k.upper():
                            target_daemon = d
                            break
                            
                if not target_daemon and self.daemon:
                    target_daemon = self.daemon
                    
                if target_daemon:
                    return {"status": "success", "config": target_daemon.config}
                return {"status": "success", "config": self.fallback_config}

        # Write surface removed (read-only dashboard): POST /config, /trigger,
        # /api/emergency_stop, /api/close_all, /api/pause_trading, /api/pause_llm.
        # Nothing reachable from a browser can alter trading behavior. Emergency
        # actions belong in the terminal / MT5, not an unauthenticated HTTP port.

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
                is_jpy = "JPY" in symbol.upper() or "XAU" in symbol.upper()
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

        @self.app.get("/api/logs/analytics")
        def get_trade_analytics(symbol: str = None, limit: int = 200):
            """Full per-trade decision + exit context from trade_analytics.jsonl:
            entry regime/MTF/displacement/S-R, exit_reason, MFE, health. Newest first."""
            import os, json as _json
            path = os.path.join("reports", "trade_analytics.jsonl")
            if not os.path.exists(path):
                return {"status": "success", "trades": []}
            out = []
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            rec = _json.loads(ln)
                        except Exception:
                            continue
                        if symbol and str(rec.get("symbol", "")).upper() != symbol.upper():
                            continue
                        out.append(rec)
                out = out[-int(limit):][::-1]  # newest first
                return {"status": "success", "trades": out}
            except Exception as e:
                return {"status": "error", "message": str(e), "trades": []}

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
                # Default subscription to the first active symbol, or EURUSD
                default_sym = self.active_symbols[0] if self.active_symbols else "EURUSD"
                self.client_subscriptions[websocket] = default_sym
            logger.info("Dashboard WS: client connected. Total: %d, Subscribed: %s", len(self.active_connections), default_sym)
            
            # Send initial active symbols list
            await websocket.send_json({
                "type": "active_symbols",
                "symbols": self.active_symbols
            })
            
            # Hydrate client immediately with latest known state for their subscribed symbol
            try:
                await self._hydrate_client(websocket, default_sym)
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
                                mt5_symbol = data.get("mt5") or pair
                                
                                # Clean the requested symbol
                                p_clean = mt5_symbol.replace("=X", "").replace("=x", "").upper()
                                
                                is_active = False
                                matched_sym = None
                                
                                with self._lock:
                                    # Find matching active symbol
                                    for sym in self.active_symbols:
                                        s_clean = sym.replace("=X", "").replace("=x", "").upper()
                                        if s_clean in p_clean or p_clean in s_clean:
                                            is_active = True
                                            matched_sym = sym
                                            break
                                            
                                    if is_active and matched_sym:
                                        self.client_subscriptions[websocket] = matched_sym
                                        
                                    elif getattr(self, "bridge_client", None) and self.bridge_client.is_connected():
                                        # Bridge mode — forward switch to the MT5 bridge
                                        is_active = True
                                        self.bridge_client.send_message({
                                            "type": "switch_pair",
                                            "symbol": mt5_symbol,
                                            "mt5": mt5_symbol,
                                        })
                                        logger.info(f"Bridge: forwarded switch_pair to {mt5_symbol}")
                                
                                if is_active:
                                    logger.info(f"Dashboard WS: client switched back to {matched_sym or mt5_symbol}, re-hydrating")
                                    if matched_sym:
                                        await self._hydrate_client(websocket, matched_sym)
                                else:
                                    active_str = ", ".join(self.active_symbols) if self.active_symbols else "None"
                                    await websocket.send_json({
                                        "type": "agent",
                                        "agent_name": "SYSTEM",
                                        "status": "active",
                                        "message": f"[WARNING] The trading daemon is currently locked to: {active_str}. Live telemetry and cognitive execution are active for those pairs only.",
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
                    self.client_subscriptions.pop(websocket, None)
                logger.info("Dashboard WS: client disconnected. Total: %d", len(self.active_connections))
            except Exception as e:
                with self._lock:
                    self.active_connections.discard(websocket)
                    self.client_subscriptions.pop(websocket, None)
                logger.debug("Dashboard WS: connection error: %s", e)

    async def _hydrate_client(self, websocket: WebSocket, symbol: str = None):
        """Send all cached state history to a newly connected client."""
        # Build fresh candle payloads OUTSIDE the global lock. Walking the daemon's
        # candle deques is the switch-latency hot spot; doing it under self._lock
        # stalled every other client's tick/account broadcast on each pair switch.
        fresh_candles = {}
        daemon = self.daemons.get(symbol) if symbol else self.daemon
        if daemon:
            for tf in ["M15", "H1", "H4"]:
                try:
                    fresh_candles[tf] = daemon._get_candles_payload(tf)
                except Exception as e:
                    logger.warning("Dashboard WS: failed to update active candle for %s: %s", tf, e)

        # 1. Thread-safely update and copy dynamic state cache
        with self._lock:
            # Fallback for old behaviour or global messages
            hist_ref = self.symbol_history.get(symbol) if symbol else self.history
            if not hist_ref and symbol:
                 hist_ref = self.history

            if fresh_candles:
                hist_ref.setdefault("candles", {}).update(fresh_candles)

            # Copy cached states to local variables under lock
            account_data = self.history["account"]  # Global
            calendar_data = self.history["calendar_data"] # Global
            
            tick_data = hist_ref.get("tick")
            regime_data = hist_ref.get("regime")
            levels_data = hist_ref.get("levels")
            candles_data = list(hist_ref.get("candles", {}).items())
            events_data = list(hist_ref.get("events", []))
            decision_data = hist_ref.get("decision")
            mode_data = hist_ref.get("mode")
            trigger_metrics_data = hist_ref.get("trigger_metrics")
            # Lifecycle fields
            trade_state_data = hist_ref.get("trade_state")
            location_context_data = hist_ref.get("location_context")

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
        """Thread-safe queueing of message broadcast across websockets based on symbol routing."""
        message = convert_numpy(message)
        msg_type = message.get("type")
        if not msg_type:
            return

        # CHANGE 10A: Broadcast throttle gate — only throttle tick messages
        import time

        if msg_type == "tick":
            # Per-symbol throttle: a shared timestamp lets high-rate symbols
            # (XAUUSD) starve slow ones (EURUSD) so their ticks never reach
            # the dashboard. Track last-broadcast per symbol instead.
            now_ms = time.perf_counter() * 1000
            _tsym = (message.get("symbol") or "_").upper()
            if not hasattr(self, "_last_tick_ms_by_symbol"):
                self._last_tick_ms_by_symbol = {}
            if now_ms - self._last_tick_ms_by_symbol.get(_tsym, 0.0) < self._broadcast_interval_ms:
                return  # drop tick, trading logic unaffected
            self._last_tick_ms_by_symbol[_tsym] = now_ms
            
        # Determine symbol routing
        sym = message.get("symbol") or message.get("mt5_symbol")
        
        # Bridge payload cleanup if necessary
        if sym:
            sym = sym.replace("=X", "").replace("=x", "").upper()
          # If no symbol specified, broadcast to all
        is_global = msg_type in ["account", "news_data", "system_log", "tick"] or not sym

        # Choose history dict and corresponding lock to update
        if is_global:
            hist_ref = self.history
            lock = self._lock
        else:
            with self._lock:
                hist_ref = self.symbol_history.setdefault(sym, self._new_symbol_history())
            lock = self._get_symbol_lock(sym)

        with lock:
            save_needed = False
            # Merge account details (positions/orders) globally to prevent dashboard flickering
            if msg_type == "account":
                sym_key = (sym or "").replace("=X", "").replace("=x", "").upper()
                if sym_key:
                    import time as _t
                    now = _t.time()
                    self.symbol_positions[sym_key] = message.get("positions", [])
                    self.symbol_orders[sym_key] = message.get("pending_orders", [])
                    if not hasattr(self, "_sym_acct_ts"):
                        self._sym_acct_ts = {}
                    self._sym_acct_ts[sym_key] = now
                    # Expire per-symbol positions/orders that haven't refreshed within
                    # the TTL, so a symbol whose trade closed (then stopped broadcasting)
                    # does not leave phantom positions in the merged account forever.
                    POS_TTL = 45.0
                    merged_positions = []
                    merged_orders = []
                    for s_k, plist in list(self.symbol_positions.items()):
                        if now - self._sym_acct_ts.get(s_k, 0.0) <= POS_TTL:
                            merged_positions.extend(plist)
                        else:
                            self.symbol_positions[s_k] = []
                    for s_k, olist in list(self.symbol_orders.items()):
                        if now - self._sym_acct_ts.get(s_k, 0.0) <= POS_TTL:
                            merged_orders.extend(olist)
                        else:
                            self.symbol_orders[s_k] = []
                    message["positions"] = merged_positions
                    message["pending_orders"] = merged_orders
                    # TOTAL floating P/L = sum of ALL open positions across every symbol — exactly
                    # what the merged positions table shows and what the terminal reports account-
                    # wide. Each daemon's own account payload carried a per-cache snapshot that
                    # flickered (and could reflect one symbol's view), so DERIVE the header's
                    # profit/equity from the merged total instead of trusting whichever wrote last.
                    # (account is is_global here → we already hold self._lock; do NOT re-lock.)
                    total_pnl = 0.0
                    for _p in merged_positions:
                        try:
                            total_pnl += float(_p.get("profit", 0) or 0)
                        except (TypeError, ValueError):
                            pass
                    # balance / margin / free_margin are account-wide; pin to the first active
                    # symbol so a stale non-authoritative snapshot cannot perturb them.
                    auth = self.active_symbols[0] if self.active_symbols else None
                    if auth and sym_key != auth.replace("=X", "").replace("=x", "").upper():
                        prev = self.history.get("account") or {}
                        for _f in ("balance", "margin", "free_margin"):
                            if prev.get(_f) is not None:
                                message[_f] = prev[_f]
                    _bal = float(message.get("balance") or 0.0)
                    _mg = float(message.get("margin") or 0.0)
                    message["profit"] = round(total_pnl, 2)
                    message["equity"] = round(_bal + total_pnl, 2)
                    message["margin_level"] = round(message["equity"] / _mg * 100, 1) if _mg else message.get("margin_level", 0.0)

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
                "risk_state",
            ]:
                hist_ref[msg_type] = message
                if msg_type == "decision":
                    save_needed = True
            elif msg_type == "news_data":
                # Calendar-only payload ?" store globally
                self.history["calendar_data"] = message
            elif msg_type in ["candle", "candles"]:
                tf = message.get("timeframe")
                if tf:
                    hist_ref.setdefault("candles", {})[tf] = message
            elif msg_type == "event":
                hist_ref.setdefault("events", []).append(message)
                if len(hist_ref["events"]) > 30:
                    hist_ref["events"].pop(0)
                save_needed = True

            if save_needed:
                self._save_session()

            if not self.active_connections:
                if msg_type == "account":
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning("broadcast(%s): no active WebSocket connections!", msg_type)
                return

        # Account is COALESCED: the merged-positions + scalars state was updated above, but we
        # do NOT forward each daemon's raw account message. All 5 daemons broadcast the same
        # account-wide equity/profit/margin sampled a few ms apart; forwarding every one made
        # the header shuffle. Instead the ~1 Hz fleet poller pushes the single canonical
        # history["account"]. See _fleet_poller.
        if msg_type == "account":
            return

        # Uvicorn and FastAPI run inside an asyncio event loop.
        # Since daemon operates in a regular thread, we bridge the call to the loop.
        if hasattr(self, "_loop") and self._loop:
            asyncio.run_coroutine_threadsafe(self._async_broadcast(message, sym, is_global), self._loop)
        elif msg_type == "account":
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("broadcast(%s): event loop not available!", msg_type)

    async def _async_broadcast(self, message: Dict[str, Any], symbol: str, is_global: bool):
        """Asynchronously send message to sockets, routing by subscription."""
        lock = self._get_symbol_lock(symbol) if not is_global else self._lock
        with lock:
            targets = []
            for ws in self.active_connections:
                if is_global or self.client_subscriptions.get(ws) == symbol:
                    targets.append(ws)
        
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                with self._lock:
                    self.active_connections.discard(ws)
                    self.client_subscriptions.pop(ws, None)

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

        fleet_thread = threading.Thread(target=self._fleet_poller, daemon=True, name="DashboardFleetPoller")
        fleet_thread.start()

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

    def _build_fleet_summary(self) -> Dict[str, Any]:
        """Aggregate the per-symbol caches into one compact fleet roll-up for
        the dashboard FLEET view. Reuses symbol_history + symbol_positions +
        the merged account. No new engine coupling."""
        rows = []
        port_realized = 0.0
        port_limit = 0.0
        port_positions = 0
        port_max = int(self.fallback_config.get("max_concurrent_positions", 5))
        port_halted = False
        for s in list(self.active_symbols):
            s_up = s.replace("=X", "").replace("=x", "").upper()
            with self._get_symbol_lock(s_up):
                h = self.symbol_history.get(s_up) or {}
                tm = h.get("trigger_metrics") or {}
                rs = h.get("risk_state") or {}
                rg = h.get("regime") or {}
                ts = h.get("trade_state") or {}
            pos_list = self.symbol_positions.get(s_up) or []
            port_realized += float(rs.get("daily_realized", 0.0) or 0.0)
            if rs.get("daily_loss_limit"):
                port_limit = float(rs.get("daily_loss_limit"))
            if rs.get("max_concurrent"):
                port_max = int(rs.get("max_concurrent"))
            if rs.get("halted"):
                port_halted = True
            port_positions += len(pos_list)
            open_t = None
            if pos_list:
                p = pos_list[0]
                open_t = {"dir": p.get("type"), "lots": p.get("volume"),
                          "pnl": p.get("profit"), "sl": p.get("sl"), "tp": p.get("tp")}
            rows.append({
                "sym": s_up,
                "engine_state": tm.get("state") or (ts.get("current_phase") if pos_list else None) or "MONITOR",
                "quality": tm.get("signal_quality"),
                "floor": rs.get("signal_quality_floor"),
                "h4_bias": tm.get("mtf_h4_bias"),
                "h1_bias": tm.get("mtf_h1_bias"),
                "regime": tm.get("regime") or rg.get("dominant"),
                "cooldown": rg.get("cooldown_remaining"),
                "in_trade": bool(pos_list),
                "phase": ts.get("current_phase"),
                "profit_pips": ts.get("current_profit_pips"),
                "open": open_t,
            })
        acct = self.history.get("account") or {}
        return {
            "type": "fleet_summary",
            "symbols": rows,
            "portfolio": {
                "day_pnl": round(port_realized, 2),
                "day_limit": port_limit or float(self.fallback_config.get("max_daily_loss_usd", 500.0)),
                "positions": port_positions,
                "max_concurrent": port_max,
                "halted": port_halted,
                "equity": acct.get("equity"),
                "balance": acct.get("balance"),
                "profit": acct.get("profit"),
            },
        }

    def _fleet_poller(self):
        """Emit fleet_summary (~1 Hz) so the FLEET view sees all symbols at once,
        independent of which symbol a client is subscribed to."""
        import time
        logger.info("Dashboard API: Fleet-summary poller started.")
        while not hasattr(self, "_poller_stop") or not self._poller_stop.is_set():
            try:
                if self.active_connections and self.active_symbols:
                    self.broadcast(self._build_fleet_summary())
                    # Push the single coalesced account (broadcast() no longer forwards the
                    # 5 daemons' raw account messages) so the header equity/profit/margin
                    # update smoothly at ~1 Hz instead of shuffling between interleaved snapshots.
                    acct = self.history.get("account")
                    if acct and hasattr(self, "_loop") and self._loop:
                        asyncio.run_coroutine_threadsafe(self._async_broadcast(acct, None, True), self._loop)
            except Exception as e:
                logger.debug("Dashboard API: fleet_summary poll failed: %s", e)
            time.sleep(1.0)

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
