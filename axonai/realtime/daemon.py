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
from axonai.realtime.event_types import EventPriority, LiveCandle, MarketEvent, EventType
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

        # Trailing stop and trade outcome tracking
        self._tracked_positions: set[int] = set()
        self._active_trade_initial_sl: dict[int, float] = {}

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
            "today_bias": me.london_open_bias,
            "today_high": me.london_range_high,
            "today_low": me.london_range_low,
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
        bhv_summary = getattr(self.live_evidence, 'level_behavior', None) or {}
        for lv in self.live_evidence.price_levels:
            if lv.is_active:
                entry = {
                    "price": lv.price,
                    "level_type": lv.level_type,
                    "direction": lv.direction,
                    "strength": lv.strength,
                    "touches": lv.touches,
                    "timeframe": lv.timeframe
                }
                # Enrich with LevelBehaviorTracker data if available
                bhv = bhv_summary.get(str(lv.price))
                if bhv:
                    entry.update({
                        "total_attacks": bhv.get("total_attacks", 0),
                        "consecutive_attacks": bhv.get("consecutive_attacks", 0),
                        "rejection_count": bhv.get("rejection_count", 0),
                        "last_rejection_velocity": bhv.get("last_rejection_velocity", 0.0),
                        "avg_rejection_velocity": bhv.get("avg_rejection_velocity", 0.0),
                        "absorption_ratio": bhv.get("absorption_ratio", 0.0),
                        "imbalance": bhv.get("imbalance", 0.0),
                        "is_absorbing": bhv.get("is_absorbing", False),
                        "attack_quality": bhv.get("attack_quality", "none"),
                        "status": bhv.get("status", "unknown"),
                    })
                levels.append(entry)
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

        # Pre-populate active positions for trailing SL tracking
        try:
            positions = mt5.positions_get(symbol=self.mt5_symbol) if mt5 else None
            if positions:
                for pos in positions:
                    self._tracked_positions.add(pos.ticket)
                    self._active_trade_initial_sl[pos.ticket] = pos.sl
                logger.info("AxonDaemon: Pre-populated %d active positions for trailing stop tracking.", len(positions))
        except Exception as pe:
            logger.warning("AxonDaemon: Failed to pre-populate active positions: %s", pe)

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

    def _on_tick(self, bid: float, ask: float, timestamp: datetime, volume: int = 1):
        """Called by TickEngine on every new tick."""
        self.event_detector.on_tick(bid, ask, timestamp)

        # Handle trailing stops and closed position logging for dryrun
        if self.config.get("realtime_dry_run", False):
            try:
                self._manage_trailing_stops(bid, ask)
                self._check_for_closed_positions(bid, ask)
            except Exception as e:
                logger.error("Error managing trailing stops / closed positions: %s", e, exc_info=True)
        
        # Broadcast tick to dashboard WebSocket
        dashboard = get_dashboard()
        if dashboard:
            imb = self.tick_engine.latest_imbalance
            ticks = self.tick_engine.tick_buffer_list
            velocity = 0.0
            spread_delta = 0.0
            collapse = False
            agg_shift = False
            absorption = False
            
            if len(ticks) >= 2:
                # Calculate velocity (last 10 seconds)
                t_10s = [t for t in ticks if (ticks[-1]['time'] - t['time']).total_seconds() <= 10.0]
                if len(t_10s) > 1:
                    price_changes = sum(abs(t_10s[i]['mid'] - t_10s[i-1]['mid']) for i in range(1, len(t_10s)))
                    time_span = (t_10s[-1]['time'] - t_10s[0]['time']).total_seconds()
                    raw_velocity = price_changes / time_span if time_span > 0 else 0.0
                    pip_unit = 0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001
                    velocity = raw_velocity / pip_unit
                
                # Calculate spread delta
                spread_delta = ticks[-1]['ask'] - ticks[-1]['bid'] - (ticks[-2]['ask'] - ticks[-2]['bid'])
                
                # Check for absorption
                t_30s = [t for t in ticks if (ticks[-1]['time'] - t['time']).total_seconds() <= 30.0]
                pip_unit = 0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001
                absorption = len(t_30s) >= 10 and velocity > 0.1 and abs(t_30s[-1]['mid'] - t_30s[0]['mid']) < pip_unit

            dashboard.broadcast({
                "type": "tick",
                "symbol": self.mt5_symbol,
                "bid": bid,
                "ask": ask,
                "spread": self.tick_engine.spread / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001),
                "time": int(timestamp.replace(tzinfo=timezone.utc).timestamp()),
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "tick_velocity": velocity,
                "tick_imbalance_10s": imb.get("imbalance_10s", 0.0),
                "tick_imbalance_60s": imb.get("imbalance_60s", 0.0),
                "tick_imbalance_300s": imb.get("imbalance_300s", 0.0),
                "tick_spread_delta": spread_delta,
                "tick_collapse": collapse,
                "tick_agg_shift": agg_shift,
                "tick_absorption": absorption,
                
                # Rule A & B Live Stats
                "rule_b_divergence": getattr(self.event_detector.peak_detector, "_last_divergence", 0.0),
                "rule_b_efficiency": getattr(self.event_detector.peak_detector, "_last_efficiency", 1.0),
                "rule_b_confirmed": getattr(self.event_detector.peak_detector, "_last_peak_confirmed", False),
                "rule_a_max_vel": getattr(self.event_detector.peak_detector, "_last_max_vel", 0.0),
                "rule_a_avg_vel": getattr(self.event_detector.peak_detector, "_last_avg_vel", 0.0)
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

            # Filter: Only allow Advanced Microstructure Peak Reversals (Rule A & Rule B)
            is_peak = event.event_type == EventType.PEAK_DETECTION
            peak_type = event.details.get("peak_type", "") if is_peak else ""
            is_exhaustion = peak_type in ("velocity_exhaustion", "microstructure_exhaustion")
            
            # S/R Proximity & Daily Trend Gate
            is_gate_passed = True
            gate_reason = ""
            if is_peak and is_exhaustion:
                dir_str = event.details.get("direction", "")
                direction = None
                if "bullish" in dir_str or "low" in peak_type:
                    direction = "BUY"
                elif "bearish" in dir_str or "high" in peak_type:
                    direction = "SELL"
                
                if direction is not None:
                    # 1. Proximity Check to ANY S/R Zone (5.0 pips)
                    active_levels = [l for l in self.live_evidence.price_levels if l.is_active]
                    closest_dist = float("inf")
                    closest_lvl = None
                    pip_mult = self.live_evidence._pip_mult
                    for lvl in active_levels:
                        dist_pips = abs(event.price - lvl.price) / pip_mult
                        if dist_pips < closest_dist:
                            closest_dist = dist_pips
                            closest_lvl = lvl
                    
                    if closest_lvl is None or closest_dist > 5.0:
                        is_gate_passed = False
                        gate_reason = f"not near any S/R zone (closest: {closest_dist:.2f} pips)"
                    else:
                        # 2. Daily Trend Alignment Check (H4 trend direction)
                        daily_trend = getattr(self.live_evidence, "trend_direction_h4", "sideways")
                        if daily_trend == "up" and direction != "BUY":
                            is_gate_passed = False
                            gate_reason = f"counter daily trend (trend: UP, trade: {direction})"
                        elif daily_trend == "down" and direction != "SELL":
                            is_gate_passed = False
                            gate_reason = f"counter daily trend (trend: DOWN, trade: {direction})"
                        else:
                            logger.info("LIVE PEAK GATE: S/R Zone Proximity + Trend Aligned! Price=%.5f is %.2f pips from %s level %.5f. Trend=%s, Trade=%s",
                                        event.price, closest_dist, closest_lvl.level_type, closest_lvl.price, daily_trend, direction)

            dashboard = get_dashboard()
            if not self.config.get("test_mode", False) and not (is_peak and is_exhaustion and is_gate_passed):
                self._events_skipped += 1
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
                        "reason": gate_reason if not is_gate_passed else "Strategy restricted to Microstructure Peaks",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            logger.info("\n" + "="*50)
            logger.info("EVENT #%d: %s", self._events_detected, event)
            logger.info("="*50)
            if hasattr(self, '_log_dry_run_event'):
                self._log_dry_run_event('event_detected', {'event_type': event.event_type.value, 'price': event.price, 'details': event.details})

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

            is_dry_run = self.config.get("realtime_dry_run", False)

            if not is_dry_run and not self.graph_executor.should_execute(event):
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
            if not is_dry_run and not ws.should_run_graph:
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

            # Fire graph or execute directly for dryrun
            if is_dry_run and not self.config.get("test_mode", False):
                self._events_fired += 1
                dir_str = event.details.get("direction", "")
                if "bullish" in dir_str or "low" in peak_type:
                    signal = "Buy"
                else:
                    signal = "Sell"
                
                logger.info("DRYRUN: Bypassing LLM Graph debate. Pure Rule A+B trigger direct signal: %s", signal)
                
                # Broadcast decision status
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
                        ticket = trade_result.get("order")
                        if ticket:
                            self._tracked_positions.add(ticket)
                            self._active_trade_initial_sl[ticket] = trade_result.get("sl")
                except Exception as ex_err:
                    logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)
                
                # Persistently log signal to file
                self._log_signal(event, ws, signal, trade_result)
                
                # Set cooldown on event detector
                cooldown = self.config.get("realtime_cooldown_seconds", 300)
                self.event_detector.set_cooldown(cooldown)
                
                # Print stats
                self._log_stats()
                continue

            self._events_fired += 1
            logger.info("FIRING GRAPH #%d for event: %s",
                        self._events_fired, event.event_type.value)
            if hasattr(self, '_log_dry_run_event'):
                self._log_dry_run_event('graph_fire', {'event_type': event.event_type.value})

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
                if hasattr(self, '_log_dry_run_event'):
                    decision_obj = final_state.get('final_trade_decision', {}) if isinstance(final_state, dict) else getattr(final_state, 'final_trade_decision', {})
                    if not isinstance(decision_obj, dict) and hasattr(decision_obj, 'dict'):
                        decision_obj = decision_obj.dict()
                    elif not isinstance(decision_obj, dict) and hasattr(decision_obj, '__dict__'):
                        decision_obj = decision_obj.__dict__
                    elif not isinstance(decision_obj, dict):
                        decision_obj = {}
                    self._log_dry_run_event('decision', {
                        'execute': decision_obj.get('execute', signal in ['Buy', 'Sell', 'Overweight', 'Underweight']),
                        'direction': decision_obj.get('direction', signal),
                        'confidence': decision_obj.get('confidence', 0),
                        'reason': decision_obj.get('reason', ''),
                        'abort_reason': decision_obj.get('abort_reason', None)
                    })

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
                if hasattr(self, '_log_dry_run_event'):
                    self._log_dry_run_event('error', {'error': str(e)})

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

    def _log_dry_run_event(self, event_type: str, details: dict):
        """Append an event to the dry run session log."""
        if not self.config.get('realtime_dry_run'):
            return
        import json, os
        from datetime import datetime
        os.makedirs('reports', exist_ok=True)
        log_path = os.path.join('reports', 'dry_run_session.jsonl')
        
        class SafeJSONEncoder(json.JSONEncoder):
            def default(self, obj):
                try:
                    return super().default(obj)
                except TypeError:
                    return str(obj)

        entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'details': details
        }
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, cls=SafeJSONEncoder) + '\n')

    def _manage_trailing_stops(self, bid: float, ask: float):
        """Manage trailing stop modifications on active MT5 positions."""
        if not mt5 or not mt5.terminal_info():
            return
            
        positions = mt5.positions_get(symbol=self.mt5_symbol)
        if not positions:
            return
            
        pip = 0.01 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 0.0001
        digits = 3 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 5
        
        for pos in positions:
            ticket = pos.ticket
            # If we don't have the initial SL recorded, initialize it from pos.sl
            if ticket not in self._active_trade_initial_sl:
                self._active_trade_initial_sl[ticket] = pos.sl
                self._tracked_positions.add(ticket)
                
            initial_sl = self._active_trade_initial_sl[ticket]
            if initial_sl <= 0.0:
                continue
                
            if pos.type == mt5.POSITION_TYPE_BUY:
                # BUY: profit is (bid - price_open). SL distance is (price_open - initial_sl)
                sl_dist = pos.price_open - initial_sl
                current_profit = bid - pos.price_open
                if sl_dist > 0 and current_profit >= 1.0 * sl_dist:
                    breakeven_sl = round(pos.price_open + 1 * pip, digits)
                    if pos.sl < breakeven_sl:
                        logger.info("AxonDaemon: Trailing SL triggered for BUY ticket %d. Modifying SL: %.5f -> %.5f",
                                    ticket, pos.sl, breakeven_sl)
                        request = {
                            "action": mt5.TRADE_ACTION_SLTP,
                            "position": ticket,
                            "symbol": self.mt5_symbol,
                            "sl": breakeven_sl,
                            "tp": pos.tp,
                        }
                        res = mt5.order_send(request)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            logger.info("AxonDaemon: Modify SL successful for ticket %d", ticket)
                            
            elif pos.type == mt5.POSITION_TYPE_SELL:
                # SELL: profit is (price_open - ask). SL distance is (initial_sl - price_open)
                sl_dist = initial_sl - pos.price_open
                current_profit = pos.price_open - ask
                if sl_dist > 0 and current_profit >= 1.0 * sl_dist:
                    breakeven_sl = round(pos.price_open - 1 * pip, digits)
                    if pos.sl > breakeven_sl or pos.sl == 0.0:
                        logger.info("AxonDaemon: Trailing SL triggered for SELL ticket %d. Modifying SL: %.5f -> %.5f",
                                    ticket, pos.sl, breakeven_sl)
                        request = {
                            "action": mt5.TRADE_ACTION_SLTP,
                            "position": ticket,
                            "symbol": self.mt5_symbol,
                            "sl": breakeven_sl,
                            "tp": pos.tp,
                        }
                        res = mt5.order_send(request)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            logger.info("AxonDaemon: Modify SL successful for ticket %d", ticket)

    def _check_for_closed_positions(self, bid: float, ask: float):
        """Detect closed positions and log outcomes."""
        if not mt5 or not mt5.terminal_info():
            return
            
        positions = mt5.positions_get(symbol=self.mt5_symbol)
        active_tickets = {p.ticket for p in positions} if positions else set()
        
        # Detect closed tickets
        closed_tickets = self._tracked_positions - active_tickets
        if not closed_tickets:
            # Still update tracked positions to capture any manually opened trades
            for t in active_tickets:
                self._tracked_positions.add(t)
            return
            
        pip = 0.01 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 0.0001
        
        for ticket in closed_tickets:
            logger.info("AxonDaemon: Detected closed position for ticket %d", ticket)
            
            # Fetch deal history for this ticket
            deals = mt5.history_deals_get(position=ticket)
            
            exit_price = 0.0
            exit_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            profit = 0.0
            pips = 0.0
            reason = "Manual Close / Unknown"
            direction = "UNKNOWN"
            volume = 0.0
            entry_price = 0.0
            
            initial_sl = self._active_trade_initial_sl.get(ticket, 0.0)
            
            if deals:
                # Find exit deal (DEAL_ENTRY_OUT)
                exit_deal = None
                entry_deal = None
                for deal in deals:
                    if deal.entry == mt5.DEAL_ENTRY_OUT:
                        exit_deal = deal
                    elif deal.entry == mt5.DEAL_ENTRY_IN:
                        entry_deal = deal
                        
                if entry_deal:
                    entry_price = entry_deal.price
                    volume = entry_deal.volume
                    direction = "BUY" if entry_deal.type == mt5.DEAL_TYPE_BUY or entry_deal.type == 0 else "SELL"
                    
                if exit_deal:
                    exit_price = exit_deal.price
                    exit_time_str = datetime.fromtimestamp(exit_deal.time).strftime("%Y-%m-%d %H:%M:%S")
                    profit = exit_deal.profit
                    comment = getattr(exit_deal, "comment", "").lower()
                    
                    # Calculate pips
                    if direction == "BUY":
                        pips = (exit_price - entry_price) / pip
                    elif direction == "SELL":
                        pips = (entry_price - exit_price) / pip
                        
                    # Determine reason
                    if "sl" in comment:
                        breakeven_approx = entry_price + (1 * pip if direction == "BUY" else -1 * pip)
                        if abs(exit_price - breakeven_approx) < 2 * pip:
                            reason = "Trailing SL Hit"
                        else:
                            reason = "Stop Loss (SL) Hit"
                    elif "tp" in comment:
                        reason = "Take Profit (TP) Hit"
                    elif "so" in comment:
                        reason = "Stop Out (SO)"
                    else:
                        reason = f"Closed ({exit_deal.comment or 'Manual'})"
                        
            # If history failed, fallback to basic estimates
            if entry_price == 0.0:
                entry_price = bid  # fallback
                
            outcome = "WIN" if pips > 0 else "LOSS" if pips < 0 else "BREAKEVEN"
            
            # Log outcome to file
            log_msg = f"TRADE CLOSED: Ticket {ticket} | {direction} | Entry: {entry_price:.5f} | Exit: {exit_price:.5f} | Profit: {profit:+.2f} | Pips: {pips:+.1f} | Reason: {reason} | Outcome: {outcome}"
            logger.info("=" * 60)
            logger.info(log_msg)
            logger.info("=" * 60)
            
            # Append outcome to reports/signals.log and jsonl
            try:
                import os, json
                os.makedirs("reports", exist_ok=True)
                payload = {
                    "timestamp": exit_time_str,
                    "type": "trade_closed",
                    "ticket": ticket,
                    "symbol": self.mt5_symbol,
                    "direction": direction,
                    "volume": volume,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "profit": profit,
                    "pips": round(pips, 1),
                    "reason": reason,
                    "outcome": outcome
                }
                with open(os.path.join("reports", "signals.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
                    
                with open(os.path.join("reports", "signals.log"), "a", encoding="utf-8") as f:
                    f.write(f"[{exit_time_str}] {log_msg}\n")
            except Exception as le:
                logger.error("Failed to write closed position log: %s", le)
                
            # Broadcast to dashboard
            dashboard = get_dashboard()
            if dashboard:
                dashboard.broadcast({
                    "type": "event",
                    "id": f"close-{ticket}",
                    "event_type": "TRADE_CLOSED",
                    "priority": "HIGH",
                    "price": exit_price,
                    "details": {
                        "ticket": ticket,
                        "direction": direction,
                        "pips": round(pips, 1),
                        "profit": profit,
                        "reason": reason,
                        "outcome": outcome
                    },
                    "timestamp": exit_time_str,
                    "status": "closed"
                })
                
            # Remove from tracking cache
            self._tracked_positions.discard(ticket)
            self._active_trade_initial_sl.pop(ticket, None)
            
            # Apply post-trade global cooldown to prevent immediate reversal trades
            # caused by our own TP/SL orders hitting the market and causing a tick climax
            cooldown_minutes = 45 if profit < 0 else 15
            logger.info("Trade closed (Profit: %.2f). Applying %d minute post-trade cooldown.", profit, cooldown_minutes)
            self.event_detector.set_cooldown(cooldown_minutes * 60)
            
        # Update tracked positions with active ones
        self._tracked_positions = active_tickets.copy()


def generate_session_summary():
    """Read reports/dry_run_session.jsonl and print a formatted summary."""
    import json, os
    from datetime import datetime

    log_path = os.path.join('reports', 'dry_run_session.jsonl')
    if not os.path.exists(log_path):
        print('No dry run session log found.')
        return

    first_time = last_time = None
    ticks = 0
    events_detected = confluence_passes = confluence_fails = graph_fires = 0
    decisions_approved = decisions_rejected = errors = sr_breaches = 0
    rejection_reasons = {}
    level_counts = {}

    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line)
                dt = datetime.fromisoformat(entry['timestamp'])
                if first_time is None: first_time = dt
                last_time = dt

                etype = entry['event_type']
                details = entry.get('details', {})

                if etype == 'error':
                    errors += 1
                elif etype == 'confluence_pass':
                    confluence_passes += 1
                elif etype == 'confluence_fail':
                    confluence_fails += 1
                elif etype == 'graph_fire':
                    graph_fires += 1
                elif etype == 'decision':
                    if details.get('execute'):
                        decisions_approved += 1
                    else:
                        decisions_rejected += 1
                        reason = details.get('abort_reason') or details.get('reason') or 'Unknown'
                        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                elif etype == 'event_detected':
                    events_detected += 1
                    if details.get('event_type') == 'LEVEL_BREACH':
                        sr_breaches += 1
                        lvl_type = details.get('details', {}).get('level_type', 'UNKNOWN')
                        price = details.get('price', 0.0)
                        key = f"{lvl_type} at {price}"
                        level_counts[key] = level_counts.get(key, 0) + 1
            except Exception:
                continue

    duration_str = '0 hours 0 minutes'
    if first_time and last_time:
        dur = last_time - first_time
        hours, rem = divmod(dur.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        duration_str = f"{int(hours)} hours {int(minutes)} minutes"

    most_active = max(level_counts.items(), key=lambda x: x[1])[0] if level_counts else 'None'

    print('\nDRY RUN SESSION SUMMARY')
    print('========================')
    print(f'Duration: {duration_str}')
    print(f'Ticks processed: {ticks} (Not tracked in this log)')
    print(f'Events detected: {events_detected}')
    print(f'Confluence gate: {confluence_passes} passed / {confluence_fails} failed')
    print(f'Graph fires: {graph_fires}')
    print('DRUCKENMILLER decisions:')
    print(f'  - APPROVED: {decisions_approved}')
    print(f'  - REJECTED: {decisions_rejected}')
    print(f'  - Top rejection reasons:')
    for reason, count in sorted(rejection_reasons.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f'      {count}x: {reason}')
    print(f'Errors: {errors}')
    print(f'SR level breaches: {sr_breaches}')
    print(f'Most active level: {most_active}\n')
