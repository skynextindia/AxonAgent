"""AxonAI real-time trading daemon.

Always-alive process that monitors MT5 tick data, detects
structural market events, and fires the multi-agent LLM graph
only when conditions demand it.
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from axonai.dataflows.mt5_data import mt5_initialize, mt5_shutdown, _to_mt5_symbol
from axonai.realtime.event_types import EventPriority, LiveCandle, MarketEvent
from axonai.realtime.tick_engine import TickEngine
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.graph_executor import GraphExecutor
from axonai.realtime.trade_executor import MT5TradeExecutor

logger = logging.getLogger(__name__)


class AxonDaemon:
    """Always-alive trading daemon.

    Lifecycle:
    1. Initialize MT5 connection
    2. Cold-start LiveWorldState + LiveMarketEvidence from historical bars
    3. Compile LangGraph once
    4. Start TickEngine thread (Layer 1)
    5. Main loop: consume events from queue, fire GraphExecutor (Layer 3)
    6. On shutdown: gracefully stop threads, close MT5
    """

    def __init__(self, symbol: str, config: dict):
        self.yf_symbol = symbol  # e.g. "EURUSD=X"
        self.mt5_symbol = _to_mt5_symbol(symbol, config)
        self.config = config
        self.event_queue: queue.Queue = queue.Queue(maxsize=100)
        self._running = False

        # Layer 1: Tick Engine
        self.tick_engine = TickEngine(self.mt5_symbol, config)

        # Layer 2: Live State + Event Detection
        self.live_state = LiveWorldState(symbol, config)
        self.live_evidence = LiveMarketEvidence(symbol)
        self.event_detector = EventDetector(
            self.live_state, self.live_evidence,
            self.event_queue, config,
        )

        # Layer 3: Graph Executor
        self.graph_executor = GraphExecutor(symbol, config)

        # Layer 4: Trade Executor
        self.trade_executor = MT5TradeExecutor(config)

        # Stats
        self._events_detected: int = 0
        self._events_fired: int = 0
        self._events_skipped: int = 0
        self._start_time: Optional[datetime] = None

    def start(self):
        """Cold start and enter main event loop."""
        self._start_time = datetime.now()
        self._running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("="*60)
        logger.info("AxonDaemon starting for %s (MT5: %s)", self.yf_symbol, self.mt5_symbol)
        logger.info("="*60)

        # 1. Initialize MT5
        if not mt5_initialize():
            logger.error("AxonDaemon: MT5 initialization failed. Cannot start.")
            return
        logger.info("Step 1/4: MT5 connected")

        # 2. Cold-start state from historical bars
        logger.info("Step 2/4: Cold-starting live state...")
        self.live_state.initialize()
        self.live_evidence.initialize()

        # Set pip multiplier on event detector
        is_jpy = "JPY" in self.mt5_symbol.upper()
        self.event_detector.set_pip_multiplier(is_jpy)
        
        # Backfill historical events to populate GUI dashboard immediately
        try:
            self.event_detector.backfill_historical_events()
        except Exception as e:
            logger.error("AxonDaemon: failed to backfill historical events: %s", e)
            
        logger.info("Step 2/4: Live state initialized")

        # 3. Compile graph
        logger.info("Step 3/4: Compiling LangGraph...")
        self.graph_executor.compile_graph()
        logger.info("Step 3/4: Graph compiled")

        # 4. Wire tick engine callbacks
        self.tick_engine.on_tick_callback = self._on_tick
        self.tick_engine.on_candle_close_callback = self._on_candle_close

        # 5. Start tick engine thread
        logger.info("Step 4/4: Starting tick engine...")
        self.tick_engine.start()
        logger.info("Step 4/4: Tick engine running")

        # Broadcast initial state to hydrate dashboard instantly
        from axonai.realtime.api_server import get_dashboard
        dashboard = get_dashboard()
        if dashboard:
            logger.info("Broadcasting initial telemetry states to dashboard...")
            # 1. Swing Levels
            me = self.live_evidence.snapshot()
            dashboard.broadcast({
                "type": "levels",
                "key_levels": me.key_levels,
                "swing_highs": me.swing_highs,
                "swing_lows": me.swing_lows,
            })
            
            # 2. Regime
            ws = self.live_state.snapshot()
            dashboard.broadcast({
                "type": "regime",
                "dominant": ws.dominant_regime,
                "confidence": ws.regime_confidence,
                "volatility": ws.volatility_regime,
                "atr": ws.atr_14_h1,
                "spread_pips": ws.spread_pips,
                "spread_safe": ws.spread_safe,
                "belief": ws.belief_score,
                "should_run_graph": ws.should_run_graph,
                "abort_reason": ws.abort_reason,
                "session": ws.session,
                "session_quality": ws.session_quality,
                # --- Daemon Status and Stats ---
                "daemon_start_time": self._start_time.timestamp() * 1000 if self._start_time else None,
                "cooldown_remaining": int(self.graph_executor.seconds_until_ready),
                "events_detected": self._events_detected,
                "events_fired": self._events_fired,
                "events_skipped": self._events_skipped,
            })
            
            # 3. Account Details
            if mt5:
                acc = mt5.account_info()
                if acc:
                    dashboard.broadcast({
                        "type": "account",
                        "balance": acc.balance,
                        "equity": acc.equity,
                        "profit": acc.profit,
                        "margin": acc.margin,
                        "free_margin": acc.margin_free,
                        "margin_level": acc.margin_level if hasattr(acc, "margin_level") else 0.0
                    })
            
            # 4. Latest Tick
            tick = mt5.symbol_info_tick(self.mt5_symbol) if mt5 else None
            if tick:
                bid = tick.bid
                ask = tick.ask
                spread = (ask - bid) / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001)
                timestamp = datetime.fromtimestamp(tick.time)
                dashboard.broadcast({
                    "type": "tick",
                    "bid": bid,
                    "ask": ask,
                    "spread": spread,
                    "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                })

        logger.info("="*60)
        logger.info("AxonDaemon LIVE. Monitoring %s in real-time.", self.yf_symbol)
        logger.info("Cooldown: %ds | Min priority: %s | Suppress Asian: %s",
                    self.config.get("realtime_cooldown_seconds", 300),
                    self.config.get("realtime_min_event_priority", "MEDIUM"),
                    self.config.get("realtime_suppress_asian", True))
        logger.info("="*60)

        # 6. Enter main event loop
        self._event_loop()

    def _on_tick(self, bid: float, ask: float, timestamp: datetime):
        """Called by TickEngine on every new tick."""
        self.event_detector.on_tick(bid, ask, timestamp)
        
        # Broadcast tick to dashboard WebSocket
        from axonai.realtime.api_server import get_dashboard
        dashboard = get_dashboard()
        if dashboard:
            dashboard.broadcast({
                "type": "tick",
                "bid": bid,
                "ask": ask,
                "spread": self.tick_engine.spread / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001),
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            })
            
            # Throttle heavier updates to once every 5 ticks
            if self.tick_engine._tick_count % 5 == 1:
                ws = self.live_state.snapshot()
                dashboard.broadcast({
                    "type": "regime",
                    "dominant": ws.dominant_regime,
                    "confidence": ws.regime_confidence,
                    "volatility": ws.volatility_regime,
                    "atr": ws.atr_14_h1,
                    "spread_pips": ws.spread_pips,
                    "spread_safe": ws.spread_safe,
                    "belief": ws.belief_score,
                    "should_run_graph": ws.should_run_graph,
                    "abort_reason": ws.abort_reason,
                    "session": ws.session,
                    "session_quality": ws.session_quality,
                    # --- Daemon Status and Stats ---
                    "daemon_start_time": self._start_time.timestamp() * 1000 if self._start_time else None,
                    "cooldown_remaining": int(self.graph_executor.seconds_until_ready),
                    "events_detected": self._events_detected,
                    "events_fired": self._events_fired,
                    "events_skipped": self._events_skipped,
                })
                
                # Fetch and broadcast MetaTrader 5 account info
                if mt5:
                    acc = mt5.account_info()
                    if acc:
                        dashboard.broadcast({
                            "type": "account",
                            "balance": acc.balance,
                            "equity": acc.equity,
                            "profit": acc.profit,
                            "margin": acc.margin,
                            "free_margin": acc.margin_free,
                            "margin_level": acc.margin_level if hasattr(acc, "margin_level") else 0.0
                        })

    def _on_candle_close(self, candle: LiveCandle):
        """Called by TickEngine when any timeframe candle closes."""
        self.event_detector.on_candle_close(candle)
        logger.debug("Candle closed: %s @ %.5f (H=%.5f L=%.5f)",
                     candle.timeframe, candle.close, candle.high, candle.low)
                     
        # Broadcast closed candle
        from axonai.realtime.api_server import get_dashboard
        dashboard = get_dashboard()
        if dashboard:
            dashboard.broadcast({
                "type": "candle",
                "timeframe": candle.timeframe,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "timestamp": candle.open_time.strftime("%Y-%m-%d %H:%M:%S")
            })
            
            # Send updated swing levels on H1 close
            if candle.timeframe == "H1":
                me = self.live_evidence.snapshot()
                dashboard.broadcast({
                    "type": "levels",
                    "key_levels": me.key_levels,
                    "swing_highs": me.swing_highs,
                    "swing_lows": me.swing_lows,
                })

    def _event_loop(self):
        """Main thread: blocks on event queue, fires graph on valid events."""
        while self._running:
            try:
                event = self.event_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            self._events_detected += 1

            logger.info("\n" + "="*50)
            logger.info("EVENT #%d: %s", self._events_detected, event)
            logger.info("="*50)

            from axonai.realtime.api_server import get_dashboard
            dashboard = get_dashboard()
            if dashboard:
                dashboard.broadcast({
                    "type": "event",
                    "id": self._events_detected,
                    "event_type": event.event_type.value,
                    "priority": event.priority.name,
                    "price": event.price,
                    "details": event.details,
                    "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "detected",
                    "events_detected": self._events_detected,
                    "events_fired": self._events_fired,
                    "events_skipped": self._events_skipped,
                })

            if not self.graph_executor.should_execute(event):
                self._events_skipped += 1
                remaining = self.graph_executor.seconds_until_ready
                logger.info("SKIPPED (cooldown=%.0fs remaining | priority=%s)",
                            remaining, event.priority.name)
                
                if dashboard:
                    dashboard.broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": f"cooldown={remaining:.0f}s remaining" if remaining > 0 else "priority threshold",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Snapshot current state
            ws = self.live_state.snapshot()
            me = self.live_evidence.snapshot()

            # Check WorldState gate
            if not ws.should_run_graph:
                self._events_skipped += 1
                logger.info("SKIPPED (WorldState gate: belief=%.2f reason=%s)",
                            ws.belief_score, ws.abort_reason)
                
                if dashboard:
                    dashboard.broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": f"WorldState gate: {ws.abort_reason}",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Fire graph
            self._events_fired += 1
            logger.info("FIRING GRAPH #%d for event: %s",
                        self._events_fired, event.event_type.value)

            # Broadcast firing event status
            if dashboard:
                dashboard.broadcast({
                    "type": "event",
                    "id": self._events_detected,
                    "event_type": event.event_type.value,
                    "priority": event.priority.name,
                    "price": event.price,
                    "details": event.details,
                    "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "firing",
                    "events_detected": self._events_detected,
                    "events_fired": self._events_fired,
                    "events_skipped": self._events_skipped,
                })
            logger.info("WorldState: regime=%s(%.2f) session=%s belief=%.2f spread=%.1f",
                        ws.dominant_regime, ws.regime_confidence,
                        ws.session, ws.belief_score, ws.spread_pips)

            try:
                # Define local dynamic LangGraph chunk streaming callback
                def chunk_callback(chunk):
                    dash = get_dashboard()
                    if not dash:
                        return
                    for node, content in chunk.items():
                        if node in ["__pregel_loop__", "checkpointer"]:
                            continue
                        
                        messages = []
                        if isinstance(content, dict) and "messages" in content:
                            messages = content["messages"]
                        elif isinstance(content, list):
                            messages = content
                        elif hasattr(content, "messages"):
                            messages = content.messages
                        
                        for message in messages:
                            from cli.main import classify_message_type, format_tool_args
                            msg_type, txt_content = classify_message_type(message)
                            
                            tool_calls_list = []
                            if hasattr(message, "tool_calls") and message.tool_calls:
                                for tc in message.tool_calls:
                                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                                    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                                    tool_calls_list.append(f"{name}({format_tool_args(args, 60)})")
                            
                            if (txt_content and txt_content.strip()) or tool_calls_list:
                                dash.broadcast({
                                    "type": "agent",
                                    "agent_name": node.replace("_", " ").title(),
                                    "status": "active",
                                    "message": txt_content or "",
                                    "tool_calls": tool_calls_list,
                                    "timestamp": datetime.now().strftime("%H:%M:%S")
                                })

                self.config["realtime_chunk_callback"] = chunk_callback

                final_state, signal = self.graph_executor.execute(event, ws, me)

                logger.info("\n" + "*"*50)
                logger.info("DECISION: %s", signal)
                logger.info("*"*50 + "\n")

                if dashboard:
                    dashboard.broadcast({
                        "type": "decision",
                        "signal": signal,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })

                # Execute order on MT5 terminal
                try:
                    trade_result = self.trade_executor.execute_signal(self.mt5_symbol, signal)
                    if trade_result:
                        logger.info("AxonDaemon: Order execution complete: %s", trade_result)
                except Exception as ex_err:
                    logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)

                # Set cooldown on event detector
                cooldown = self.config.get("realtime_cooldown_seconds", 300)
                self.event_detector.set_cooldown(cooldown)

            except Exception as e:
                logger.error("Graph execution failed: %s", e, exc_info=True)

            # Print stats
            self._log_stats()

    def _log_stats(self):
        """Log daemon statistics."""
        if self._start_time:
            uptime = datetime.now() - self._start_time
            logger.info(
                "STATS: uptime=%s | ticks=%d | events_detected=%d | "
                "events_fired=%d | events_skipped=%d | cooldown_remaining=%.0fs",
                str(uptime).split(".")[0],
                self.tick_engine._tick_count,
                self._events_detected,
                self._events_fired,
                self._events_skipped,
                self.graph_executor.seconds_until_ready,
            )

    def _signal_handler(self, signum, frame):
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self.stop()

    def stop(self):
        """Graceful shutdown."""
        logger.info("AxonDaemon shutting down...")
        self._running = False
        self.tick_engine.stop()
        self.tick_engine.join(timeout=5)
        mt5_shutdown()
        self._log_stats()
        logger.info("AxonDaemon stopped.")

    @property
    def is_running(self) -> bool:
        return self._running
