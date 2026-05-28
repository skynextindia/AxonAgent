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
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from axonai.dataflows.mt5_data import mt5_initialize, mt5_shutdown, _to_mt5_symbol, get_broker_tz_offset
from axonai.realtime.event_types import EventPriority, LiveCandle, MarketEvent
from axonai.realtime.tick_engine import TickEngine
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.graph_executor import GraphExecutor
from axonai.realtime.trade_executor import MT5TradeExecutor
from axonai.realtime.api_server import get_dashboard
from cli.stats_handler import StatsCallbackHandler

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
        clean_sym = symbol.replace("=X", "").replace("=x", "").strip()
        self.yf_symbol = clean_sym + "=X"  # e.g. "EURUSD=X"
        self.mt5_symbol = _to_mt5_symbol(symbol, config)
        self.config = config
        self.offset_hours = 0
        self.tz = timezone.utc
        self.event_queue: queue.Queue = queue.Queue(maxsize=100)
        self._running = False

        # Layer 1: Tick Engine
        self.tick_engine = TickEngine(self.mt5_symbol, config)

        # Layer 2: Live State + Event Detection
        self.live_state = LiveWorldState(symbol, config)
        self.live_evidence = LiveMarketEvidence(symbol, config)
        self.event_detector = EventDetector(
            self.live_state, self.live_evidence,
            self.event_queue, config,
        )

        # Layer 3: Graph Executor
        self.stats_handler = StatsCallbackHandler()
        self.graph_executor = GraphExecutor(symbol, config, callbacks=[self.stats_handler])

        # Layer 4: Trade Executor
        self.trade_executor = MT5TradeExecutor(config)

        # Stats
        self._events_detected: int = 0
        self._events_fired: int = 0
        self._events_skipped: int = 0
        self._start_time: Optional[datetime] = None

    @staticmethod
    def _get_session_details(now_utc: datetime) -> list:
        """Compute active/inactive state + progress for each forex session with dynamic DST.
        """
        year = now_utc.year
        utc_hour = now_utc.hour + now_utc.minute / 60.0

        # US DST: 2nd Sunday in March to 1st Sunday in Nov
        dst_start_us = datetime(year, 3, 8)
        while dst_start_us.weekday() != 6:
            dst_start_us += timedelta(days=1)
        dst_end_us = datetime(year, 11, 1)
        while dst_end_us.weekday() != 6:
            dst_end_us += timedelta(days=1)
        is_us_dst = dst_start_us.date() <= now_utc.date() < dst_end_us.date()

        # EU DST: last Sunday in March to last Sunday in Oct
        dst_start_eu = datetime(year, 3, 31)
        while dst_start_eu.weekday() != 6:
            dst_start_eu -= timedelta(days=1)
        dst_end_eu = datetime(year, 10, 31)
        while dst_end_eu.weekday() != 6:
            dst_end_eu -= timedelta(days=1)
        is_eu_dst = dst_start_eu.date() <= now_utc.date() < dst_end_eu.date()

        # AEDT active: first Sunday in October to first Sunday in April
        dst_end_au = datetime(year, 4, 1)
        while dst_end_au.weekday() != 6:
            dst_end_au += timedelta(days=1)
        dst_start_au = datetime(year, 10, 1)
        while dst_start_au.weekday() != 6:
            dst_start_au += timedelta(days=1)
        is_au_dst = now_utc.date() < dst_end_au.date() or now_utc.date() >= dst_start_au.date()

        syd_open = 21.0 if is_au_dst else 22.0
        syd_close = 6.0 if is_au_dst else 7.0
        
        ldn_open = 7.0 if is_eu_dst else 8.0
        ldn_close = 15.0 if is_eu_dst else 16.0
        
        ny_open = 12.0 if is_us_dst else 13.0
        ny_close = 20.0 if is_us_dst else 21.0

        sessions_def = [
            {"name": "Sydney",   "open": syd_open, "close": syd_close, "duration": 9.0,  "color": "#00bfff"},
            {"name": "Tokyo",    "open": 0.0,      "close": 9.0,       "duration": 9.0,  "color": "#ff6b9d"},
            {"name": "London",   "open": ldn_open, "close": ldn_close, "duration": 8.0,  "color": "#9d00ff"},
            {"name": "New York", "open": ny_open,  "close": ny_close,  "duration": 9.0,  "color": "#00ff66"},
        ]
        result = []
        for s in sessions_def:
            o, c, dur = s["open"], s["close"], s["duration"]
            # Handle wrap-around
            if o > c:  # wraps midnight
                active = utc_hour >= o or utc_hour < c
                elapsed = (utc_hour - o) if utc_hour >= o else (utc_hour + 24.0 - o)
            else:
                active = o <= utc_hour < c
                elapsed = utc_hour - o if active else 0.0
            progress = min(max(elapsed / dur, 0.0), 1.0) if active else 0.0
            remaining_h = max(dur - elapsed, 0.0) if active else 0.0
            result.append({
                "name": s["name"],
                "active": active,
                "open_utc": o,
                "close_utc": c,
                "progress": round(progress, 3),
                "remaining_min": round(remaining_h * 60),
                "color": s["color"],
            })
        return result

    def _get_regime_payload(self) -> dict:
        ws = self.live_state.snapshot()
        me = self.live_evidence.snapshot()
        
        # Calculate M15 trend dynamically
        trend_m15 = "sideways"
        if self.live_evidence._m15_candles and len(self.live_evidence._m15_candles) >= 20:
            m15_closes = [c.close for c in self.live_evidence._m15_candles]
            k = 2.0 / 21.0
            ema20 = m15_closes[0]
            for c in m15_closes[1:]:
                ema20 = c * k + ema20 * (1 - k)
            trend_m15 = "up" if m15_closes[-1] > ema20 else "down"

        # Compute detailed session data from real UTC clock with active DST
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        year = now_utc.year
        session_details = self._get_session_details(now_utc)
        
        # NY DST checks for range calculation
        dst_start_us = datetime(year, 3, 8)
        while dst_start_us.weekday() != 6:
            dst_start_us += timedelta(days=1)
        dst_end_us = datetime(year, 11, 1)
        while dst_end_us.weekday() != 6:
            dst_end_us += timedelta(days=1)
        is_us_dst = dst_start_us.date() <= now_utc.date() < dst_end_us.date()

        # London DST checks for range calculation
        dst_start_eu = datetime(year, 3, 31)
        while dst_start_eu.weekday() != 6:
            dst_start_eu -= timedelta(days=1)
        dst_end_eu = datetime(year, 10, 31)
        while dst_end_eu.weekday() != 6:
            dst_end_eu -= timedelta(days=1)
        is_eu_dst = dst_start_eu.date() <= now_utc.date() < dst_end_eu.date()

        ldn_open = 7.0 if is_eu_dst else 8.0
        ldn_close = 15.0 if is_eu_dst else 16.0
        ny_open = 12.0 if is_us_dst else 13.0
        ny_close = 20.0 if is_us_dst else 21.0
            
        # Real-time session ranges update using latest tick price
        current_bid = self.tick_engine.latest_bid
        if current_bid > 0.0:
            utc_hour = now_utc.hour + now_utc.minute / 60.0
            if 0 <= utc_hour < 8.0:
                if self.live_evidence._evidence.asian_range_high == 0.0 or current_bid > self.live_evidence._evidence.asian_range_high:
                    self.live_evidence._evidence.asian_range_high = current_bid
                if self.live_evidence._evidence.asian_range_low == 0.0 or current_bid < self.live_evidence._evidence.asian_range_low:
                    self.live_evidence._evidence.asian_range_low = current_bid
            elif ldn_open <= utc_hour < ldn_close:
                if self.live_evidence._evidence.london_range_high == 0.0 or current_bid > self.live_evidence._evidence.london_range_high:
                    self.live_evidence._evidence.london_range_high = current_bid
                if self.live_evidence._evidence.london_range_low == 0.0 or current_bid < self.live_evidence._evidence.london_range_low:
                    self.live_evidence._evidence.london_range_low = current_bid
            elif ny_open <= utc_hour < ny_close:
                if self.live_evidence._evidence.ny_range_high == 0.0 or current_bid > self.live_evidence._evidence.ny_range_high:
                    self.live_evidence._evidence.ny_range_high = current_bid
                if self.live_evidence._evidence.ny_range_low == 0.0 or current_bid < self.live_evidence._evidence.ny_range_low:
                    self.live_evidence._evidence.ny_range_low = current_bid
            # Refresh snapshot to reflect the updated tick values
            me = self.live_evidence.snapshot()

        # Check if market is closed (weekend or holiday)
        from axonai.dataflows.mt5_data import get_broker_tz_offset
        offset_hours = get_broker_tz_offset(self.mt5_symbol)
        broker_now = now_utc + timedelta(hours=offset_hours)
        market_closed = broker_now.weekday() in (5, 6)
        
        # Calculate resume time (Sunday 22:00 UTC)
        days_until_sunday = (6 - now_utc.weekday()) % 7
        resume_dt = now_utc.replace(hour=22, minute=0, second=0, microsecond=0) + timedelta(days=days_until_sunday)
        if now_utc >= resume_dt:
            resume_dt += timedelta(days=7)
        market_resume_timestamp = int(resume_dt.timestamp())
        
        # Holiday heuristic check: if weekday but no ticks for >3 hours, mark closed
        if not market_closed and mt5:
            tick = mt5.symbol_info_tick(self.mt5_symbol)
            if tick is not None:
                last_tick_utc = datetime.fromtimestamp(tick.time, tz=timezone.utc) - timedelta(hours=offset_hours)
                if (now_utc - last_tick_utc).total_seconds() > 10800:
                    market_closed = True
                    # Next weekday at 22:00 UTC
                    resume_dt = now_utc.replace(hour=22, minute=0, second=0, microsecond=0) + timedelta(days=1)
                    while resume_dt.weekday() in (5, 6):
                        resume_dt += timedelta(days=1)
                    market_resume_timestamp = int(resume_dt.timestamp())

        return {
            "type": "regime",
            "symbol": self.mt5_symbol,
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
            "session_details": session_details,
            "market_closed": market_closed,
            "market_resume_timestamp": market_resume_timestamp,
            # --- Daemon Status and Stats ---
            "daemon_start_time": self._start_time.timestamp() * 1000 if self._start_time else None,
            "cooldown_remaining": int(self.graph_executor.seconds_until_ready),
            "events_detected": self._events_detected,
            "events_fired": self._events_fired,
            "events_skipped": self._events_skipped,
            # -- Enriched indicators --
            "regime_scores": dict(ws.regime_scores) if ws.regime_scores else {},
            "eur_strength": ws.eur_strength,
            "usd_strength": ws.usd_strength,
            "hours_since_london_open": ws.hours_since_london_open,
            "trend_h4": me.trend_direction_h4,
            "trend_h1": me.trend_direction_h1,
            "trend_m15": trend_m15,
            "rsi_h1": me.rsi_h1,
            "macd_signal_h1": me.macd_signal_h1,
            "london_open_bias": me.london_open_bias,
            "asian_range_high": me.asian_range_high,
            "asian_range_low": me.asian_range_low,
            "london_range_high": me.london_range_high,
            "london_range_low": me.london_range_low,
            "ny_range_high": me.ny_range_high,
            "ny_range_low": me.ny_range_low,
            # --- Token Consumption and Stats ---
            "tokens_in": self.stats_handler.tokens_in,
            "tokens_out": self.stats_handler.tokens_out,
            "tokens_total": self.stats_handler.tokens_in + self.stats_handler.tokens_out,
            "llm_calls": self.stats_handler.llm_calls,
            "tool_calls": self.stats_handler.tool_calls
        }

    def _get_levels_payload(self) -> dict:
        levels = []
        for lv in self.live_evidence.price_levels:
            if lv.is_active:
                levels.append({
                    "price": lv.price,
                    "level_type": lv.level_type,
                    "direction": lv.direction,
                    "strength": lv.strength,
                    "touches": lv.touches,
                    "timeframe": lv.timeframe
                })
        return {
            "type": "levels",
            "price_levels": levels
        }


    def _get_candles_payload(self, timeframe: str) -> dict:
        if timeframe == "M15":
            target_deque = self.live_evidence._m15_candles
        elif timeframe == "H1":
            target_deque = self.live_evidence._h1_candles
        elif timeframe == "H4":
            target_deque = self.live_evidence._h4_candles
        else:
            target_deque = self.live_evidence._m15_candles

        candles_list = [{
            "time": int(c.open_time.replace(tzinfo=timezone.utc).timestamp()),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close
        } for c in target_deque]
        
        # Include active in-progress candle if available in TickEngine
        if hasattr(self, "tick_engine") and self.tick_engine:
            builder = self.tick_engine.candle_builders.get(timeframe)
            if builder and builder.current:
                cur = builder.current
                cur_time = int(cur.open_time.replace(tzinfo=timezone.utc).timestamp())
                # Avoid duplicating if the last candle in history matches cur_time
                if not candles_list or candles_list[-1]["time"] != cur_time:
                    candles_list.append({
                        "time": cur_time,
                        "open": cur.open,
                        "high": cur.high,
                        "low": cur.low,
                        "close": cur.close
                    })
                    
        return {
            "type": "candles",
            "timeframe": timeframe,
            "candles": candles_list
        }

    def _get_account_payload(self) -> Optional[dict]:
        if not mt5:
            return None
        try:
            acc = mt5.account_info()
            if not acc:
                return None
            
            positions = mt5.positions_get(symbol=self.mt5_symbol)
            pos_list = []
            if positions:
                for p in positions:
                    pos_list.append({
                        "ticket": int(p.ticket),
                        "symbol": p.symbol,
                        "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                        "volume": float(p.volume),
                        "price_open": float(p.price_open),
                        "price_current": float(p.price_current),
                        "sl": float(p.sl),
                        "tp": float(p.tp),
                        "profit": float(p.profit)
                    })
            
            return {
                "type": "account",
                "balance": acc.balance,
                "equity": acc.equity,
                "profit": acc.profit,
                "margin": acc.margin,
                "free_margin": acc.margin_free,
                "margin_level": acc.margin_level if hasattr(acc, "margin_level") else 0.0,
                "positions": pos_list
            }
        except Exception as e:
            logger.warning("Failed to retrieve MT5 account info: %s", e)
            return None

    def start(self):
        """Cold start and enter main event loop."""
        self._start_time = datetime.now()
        self._running = True

        # Register signal handlers for graceful shutdown
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except ValueError as e:
            logger.warning("Could not register signal handlers (not in main thread): %s", e)

        logger.info("="*60)
        logger.info("AxonDaemon starting for %s (MT5: %s)", self.yf_symbol, self.mt5_symbol)
        logger.info("="*60)

        # Register daemon with dashboard server if available
        dashboard = get_dashboard()
        if dashboard:
            dashboard.daemon = self

        # 1. Initialize MT5
        if not mt5_initialize():
            logger.error("AxonDaemon: MT5 initialization failed. Cannot start.")
            return
        logger.info("Step 1/4: MT5 connected")

        # Now that MT5 is connected, dynamically detect active broker offset!
        from axonai.dataflows.mt5_data import _ensure_symbol_visible
        _ensure_symbol_visible(self.mt5_symbol)
        self.offset_hours = get_broker_tz_offset(self.mt5_symbol)
        self.tz = timezone(timedelta(hours=self.offset_hours))
        logger.info("Step 1/4: Broker timezone offset detected: %d hours", self.offset_hours)

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
        dashboard = get_dashboard()
        if dashboard:
            logger.info("Broadcasting initial telemetry states to dashboard...")
            # 1. Swing Levels
            dashboard.broadcast(self._get_levels_payload())
            
            # 2. Regime
            dashboard.broadcast(self._get_regime_payload())
            
            # 3. Candles (M15 & H1)
            dashboard.broadcast(self._get_candles_payload("M15"))
            dashboard.broadcast(self._get_candles_payload("H1"))
            
            # 4. Account Details
            acc_payload = self._get_account_payload()
            if acc_payload:
                dashboard.broadcast(acc_payload)
            
            # 5. Latest Tick
            tick = mt5.symbol_info_tick(self.mt5_symbol) if mt5 else None
            if tick:
                bid = tick.bid
                ask = tick.ask
                spread = (ask - bid) / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001)
                timestamp = datetime.utcfromtimestamp(tick.time)
                dashboard.broadcast({
                    "type": "tick",
                    "symbol": self.mt5_symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread": spread,
                    "time": int(timestamp.replace(tzinfo=timezone.utc).timestamp()),
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
        # TEST TRIGGER: Queue a mock event immediately to show the user how the debate works in real-time
        from axonai.realtime.event_types import MarketEvent, EventType, EventPriority
        self.event_queue.put(MarketEvent(
            event_type=EventType.LEVEL_BREACH,
            priority=EventPriority.HIGH,
            timestamp=datetime.now(),
            symbol=self.yf_symbol,
            price=1.16282,
            details={
                "level_type": "PDH",
                "level_price": 1.16282,
                "strength": 0.7,
                "touches": 2,
                "direction": "resistance",
                "distance_pips": 0.0
            }
        ))

        self._event_loop()

    def _on_tick(self, bid: float, ask: float, timestamp: datetime):
        """Called by TickEngine on every new tick."""
        self.event_detector.on_tick(bid, ask, timestamp)
        
        # Broadcast tick to dashboard WebSocket
        dashboard = get_dashboard()
        if dashboard:
            dashboard.broadcast({
                "type": "tick",
                "symbol": self.mt5_symbol,
                "bid": bid,
                "ask": ask,
                "spread": self.tick_engine.spread / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001),
                "time": int(timestamp.replace(tzinfo=timezone.utc).timestamp()),
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            })
            
            # Throttle heavier updates to once every 5 ticks
            if self.tick_engine._tick_count % 5 == 1:
                dashboard.broadcast(self._get_regime_payload())
                
                # Fetch and broadcast MetaTrader 5 account info
                acc_payload = self._get_account_payload()
                if acc_payload:
                    dashboard.broadcast(acc_payload)

    def _on_candle_close(self, candle: LiveCandle):
        """Called by TickEngine when any timeframe candle closes."""
        self.event_detector.on_candle_close(candle)
        logger.debug("Candle closed: %s @ %.5f (H=%.5f L=%.5f)",
                     candle.timeframe, candle.close, candle.high, candle.low)
                     
        # Broadcast closed candle
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
                "time": int(candle.open_time.replace(tzinfo=timezone.utc).timestamp()),
                "timestamp": candle.open_time.strftime("%Y-%m-%d %H:%M:%S")
            })
            
            # Send updated candles array for structural timeframes
            if candle.timeframe in ("M15", "H1"):
                dashboard.broadcast(self._get_candles_payload(candle.timeframe))
                dashboard.broadcast(self._get_levels_payload())
                dashboard.broadcast(self._get_regime_payload())

    def _event_loop(self):
        """Main thread: blocks on event queue, fires graph on valid events."""
        import time as pytime
        last_stats_time = pytime.time()
        while self._running:
            try:
                event = self.event_queue.get(timeout=1.0)
            except queue.Empty:
                if pytime.time() - last_stats_time > 10.0:
                    self._log_stats()
                    last_stats_time = pytime.time()
                continue

            self._events_detected += 1

            logger.info("\n" + "="*50)
            logger.info("EVENT #%d: %s", self._events_detected, event)
            logger.info("="*50)

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
                    AGENT_NAME_MAP = {
                        "Market Analyst": "WYCKOFF",
                        "Fundamentals Analyst": "KEYNES",
                        "News Analyst": "REUTERS",
                        "Sentiment Analyst": "LIVERMORE",
                        "Bull Researcher": "BUFFETT",
                        "Bear Researcher": "SOROS",
                        "Research Manager": "MUNGER",
                        "Trader": "TUDOR",
                        "Aggressive Analyst": "SIMONS",
                        "Conservative Analyst": "DALIO",
                        "Neutral Analyst": "MARKS",
                        "Portfolio Manager": "DRUCKENMILLER"
                    }
                    
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
                                    "agent_name": AGENT_NAME_MAP.get(node, node),
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
                trade_result = None
                try:
                    trade_result = self.trade_executor.execute_signal(self.mt5_symbol, signal, self.live_state)
                    if trade_result:
                        logger.info("AxonDaemon: Order execution complete: %s", trade_result)
                except Exception as ex_err:
                    logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)

                # Persistently log signal to file
                self._log_signal(event, ws, signal, trade_result)

                # Set cooldown on event detector
                cooldown = self.config.get("realtime_cooldown_seconds", 300)
                self.event_detector.set_cooldown(cooldown)

            except Exception as e:
                logger.error("Graph execution failed: %s", e, exc_info=True)

            # Print stats
            self._log_stats()

    def _log_signal(self, event, ws, signal, trade_result):
        """Persistently log every generated signal to reports/signals.jsonl and reports/signals.log."""
        import json
        import os
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_payload = {
            "timestamp": timestamp_str,
            "ticker": self.yf_symbol,
            "mt5_symbol": self.mt5_symbol,
            "event_type": event.event_type.value,
            "event_priority": event.priority.name,
            "event_price": event.price,
            "event_details": event.details,
            "dominant_regime": ws.dominant_regime,
            "regime_confidence": ws.regime_confidence,
            "volatility": ws.volatility_regime,
            "spread_pips": ws.spread_pips,
            "decision": signal,
            "trade_result": trade_result
        }
        
        # Ensure reports dir exists
        os.makedirs("reports", exist_ok=True)
        
        # Append to signals.jsonl
        try:
            with open(os.path.join("reports", "signals.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(log_payload) + "\n")
        except Exception as e:
            logger.error("Failed to append to signals.jsonl: %s", e)
            
        # Append to signals.log
        try:
            with open(os.path.join("reports", "signals.log"), "a", encoding="utf-8") as f:
                f.write(
                    f"[{timestamp_str}] TICKER: {self.yf_symbol} | EVENT: {event.event_type.value} ({event.priority.name}) "
                    f"| REGIME: {ws.dominant_regime} ({ws.regime_confidence:.2f}) | DECISION: {signal} "
                    f"| RESULT: {trade_result}\n"
                )
        except Exception as e:
            logger.error("Failed to append to signals.log: %s", e)

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
        try:
            self.tick_engine.join(timeout=5)
        except RuntimeError:
            pass
        mt5_shutdown()
        self._log_stats()
        logger.info("AxonDaemon stopped.")

    @property
    def is_running(self) -> bool:
        return self._running
