"""AxonAI real-time trading daemon.

Always-alive process that monitors MT5 tick data, detects structural market
events with pure math, and routes actionable events straight to the MT5 trade
executor — one daemon instance per currency pair.
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

from axonai.dataflows.mt5_data import mt5_initialize, mt5_shutdown, _to_mt5_symbol, get_broker_tz_offset, mt5_lock
from axonai.realtime.event_types import EventPriority, LiveCandle, MarketEvent, EventType
from axonai.realtime.tick_engine import TickEngine
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence, get_dst_session_hours
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.trade_executor import MT5TradeExecutor
from axonai.realtime.api_server import get_dashboard
from cli.stats_handler import StatsCallbackHandler
from axonai.realtime.news_guard import NewsGuard
from axonai.realtime.alerts import send_alert
from axonai.realtime.session_tuner import SessionTuner
from axonai.default_config import resolve_symbol_config, _canonical_symbol

logger = logging.getLogger(__name__)


class AxonDaemon:
    """Always-alive trading daemon.

    Lifecycle:
    1. Initialize MT5 connection
    2. Cold-start LiveWorldState + LiveMarketEvidence from historical bars
    3. Start TickEngine thread (Layer 1)
    4. Main loop: consume detected events from the queue and execute
       trade signals via MT5TradeExecutor (Layer 3)
    5. On shutdown: gracefully stop threads, close MT5
    """

    def __init__(self, symbol: str, config: dict, risk_guard=None,
                 correlation_engine=None, supervisor=None, config_overrides=None):
        # Initialize MT5 early so symbol resolution can query active terminal info
        mt5_initialize(
            terminal_path=config.get("mt5_terminal_path"),
            login=config.get("mt5_login"),
            password=config.get("mt5_password"),
            server=config.get("mt5_server")
        )
        clean_sym = symbol.replace("=X", "").replace("=x", "").strip()
        self.yf_symbol = clean_sym + "=X"  # e.g. "EURUSD=X"
        self.mt5_symbol = _to_mt5_symbol(symbol, config)
        # Overlay per-pair calibration so every component built below (executor,
        # live state, event detector, session tuner) sees this symbol's
        # calibrated values (pip size, magic, risk %, SL/TP, trailing mults).
        config = resolve_symbol_config(config, self.mt5_symbol)
        if config_overrides:
            # Supervisor-supplied per-pair overrides (e.g. vol-ratio stop floor).
            config = {**config, **config_overrides}
        self.config = config
        # Multi-pair wiring (all optional; None → standalone single-pair mode).
        self.supervisor = supervisor
        self.correlation_engine = correlation_engine
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

        # Layer 4: Trade Executor (magic + sizing from calibrated config; shares
        # the supervisor's account-global RiskGuard when running multi-pair).
        self.trade_executor_opt = MT5TradeExecutor(self.config, risk_guard=risk_guard)
        self.trade_executor = self.trade_executor_opt  # Default fallback reference

        # Trailing stop and trade outcome tracking
        self._tracked_positions: set[int] = set()
        self._active_trade_initial_sl: dict[int, float] = {}
        self._active_trade_system: dict[int, str] = {}
        self._active_trade_atr: dict[int, float] = {}
        self._active_trade_peak_price: dict[int, float] = {}
        self._sl_fail_alert_ts: dict[int, float] = {}   # per-ticket throttle for SL-modify-failure alerts

        # Layer 5: Economic news calendar guard
        self.news_guard = NewsGuard(config)
        self._last_session: Optional[str] = None

        # EOD hard-flat + daily-reset state. The trading day is treated as
        # rolling at the NY liquidity-end boundary (ny_close); both the EOD
        # re-entry block and the per-pair SL lockout key off this.
        self._last_trading_day = None       # trading day currently in effect
        self._eod_flat_tradeday = None      # trading day we last hard-flatted
        self._eod_flat_blocked = False      # bar entries during the pre-close window
        self._sl_locked_out = False         # per-pair SL lockout (Phase 2, live)

        # Self-configuring session selector (learns per-session movement of this pair)
        self.auto_sessions = bool(config.get("realtime_auto_sessions", False))
        self.session_tuner = SessionTuner(config, self.mt5_symbol) if self.auto_sessions else None

        # Stats
        self._events_detected: int = 0
        self._events_fired: int = 0
        self._events_skipped: int = 0
        self._start_time: Optional[datetime] = None

        # ── A→B order mirror (lead side) + execution-node mode (follower side) ──
        # Lead: a MirrorClient forwards each entry/close decision (set externally
        # by the supervisor / launcher when ``mirror_enabled``). Follower: this
        # process only executes decisions injected from the lead — its own
        # detection is gated off, and it re-sizes to THIS account with a distinct
        # magic and its own conservative lot ceiling.
        # Prop-firm guard state (inert unless prop_guard_enabled on this process).
        self._prop_flattened = False
        self._last_prop_check = 0.0

        self.mirror_client = None
        self._exec_node = bool(config.get("exec_node_mode", False))
        self._orders_routed = 0          # exec node: entries received from the lead
        if self._exec_node:
            self._apply_exec_node_overrides()

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

    def _current_active_sessions(self) -> list:
        """Effective tradable sessions: tuner (if warmed up) else config list."""
        learned = self.session_tuner.active_sessions() if self.session_tuner is not None else None
        if learned is not None:
            return learned
        return self.config.get(
            "realtime_active_sessions",
            ["asian", "london", "overlap", "newyork", "rollover"],
        )

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
            "active_sessions": self._current_active_sessions(),
            "auto_sessions": self.auto_sessions,
            "market_closed": market_closed,
            "market_resume_timestamp": market_resume_timestamp,
            # --- Daemon Status and Stats ---
            "daemon_start_time": self._start_time.timestamp() * 1000 if self._start_time else None,
            "cooldown_remaining": int(max(0.0, (self.event_detector._cooldown_until - datetime.now(timezone.utc if self.event_detector._cooldown_until.tzinfo else None)).total_seconds()) if hasattr(self.event_detector, "_cooldown_until") else 0.0),
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
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "llm_calls": 0,
            "tool_calls": 0
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
            dashboard.register_daemon(self.mt5_symbol, self)

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
                    # Re-seed the correlation book too — without this the engine
                    # starts blind after a restart and the dollar-direction lock
                    # would silently allow a conflicting entry while positions are open.
                    if self.correlation_engine is not None:
                        self.correlation_engine.register_position(
                            self.mt5_symbol,
                            "Buy" if pos.type == 0 else "Sell",   # mt5.POSITION_TYPE_BUY == 0
                            pos.volume, pos.price_open, pos.ticket)
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
            
        # Initialize news guard and session tracking state
        self.news_guard.refresh()
        self._last_session = self.live_state._state.session if self.live_state._state else None

        logger.info("Step 2/4: Live state initialized")

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
            self._broadcast(self._get_levels_payload())
            
            # 2. Regime
            self._broadcast(self._get_regime_payload())
            
            # 3. Candles (M15 & H1)
            self._broadcast(self._get_candles_payload("M15"))
            self._broadcast(self._get_candles_payload("H1"))
            
            # 4. Account Details
            acc_payload = self._get_account_payload()
            if acc_payload:
                self._broadcast(acc_payload)
            
            # 5. Latest Tick
            tick = mt5.symbol_info_tick(self.mt5_symbol) if mt5 else None
            if tick:
                bid = tick.bid
                ask = tick.ask
                spread = (ask - bid) / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001)
                timestamp = datetime.utcfromtimestamp(tick.time)
                self._broadcast({
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

        self._event_loop()

    def _report_path(self, filename: str) -> str:
        """Build a ``reports/`` path tagged for THIS instance.

        The lead and the execution node run from the same working directory, so
        an untagged filename means two OS processes appending to one file. That
        is not just torn writes: the payloads carry raw ``profit`` from two
        DIFFERENT accounts with no account field, so anything aggregating the
        file (the dashboard trades view) would sum a live account and a prop
        account together. ``instance_tag`` is "" for the lead — it keeps the
        historical untagged names so its existing history stays continuous.
        """
        import os
        tag = self.config.get("instance_tag", "") or ""
        if tag:
            stem, ext = os.path.splitext(filename)
            filename = f"{stem}{tag}{ext}"
        return os.path.join("reports", filename)

    def _broadcast(self, message: dict) -> None:
        """Broadcast a dashboard message tagged with this daemon's symbol.

        In multi-pair mode the dashboard routes/filters telemetry by ``symbol``;
        a single-pair daemon is unaffected.
        """
        dashboard = get_dashboard()
        if not dashboard:
            return
        try:
            if isinstance(message, dict):
                message.setdefault("symbol", self.mt5_symbol)
            dashboard.broadcast(message)
        except Exception as e:
            logger.debug("dashboard broadcast failed: %s", e)

    def _on_tick(self, bid: float, ask: float, timestamp: datetime, volume: int = 1):
        """Called by TickEngine on every new tick."""
        now_utc = datetime.now(timezone.utc)

        # Daily reset (trading day rolls at the NY liquidity-end boundary):
        # clears the EOD re-entry block and the per-pair SL lockout.
        try:
            self._check_daily_reset(now_utc)
        except Exception as e:
            logger.error("Error in daily reset check: %s", e, exc_info=True)

        # EOD hard-flat: close ALL positions near the NY-session liquidity end.
        try:
            self._check_eod_hard_flat(now_utc)
        except Exception as e:
            logger.error("Error in EOD hard-flat check: %s", e, exc_info=True)

        # Prop-firm limits: feed live equity + flatten on breach (no-op unless
        # prop_guard_enabled on THIS account's process).
        try:
            self._check_prop_risk()
        except Exception as e:
            logger.error("Error in prop risk check: %s", e, exc_info=True)

        # EOD (legacy profit-only) force-close check on session transition
        try:
            self._check_eod_close(bid, ask)
        except Exception as e:
            logger.error("Error in EOD close check: %s", e, exc_info=True)

        # News Event force-close check
        try:
            self._check_pre_news_close(bid, ask)
        except Exception as e:
            logger.error("Error in Pre-News close check: %s", e, exc_info=True)

        self.event_detector.is_in_trade = len(self._tracked_positions) > 0
        self.event_detector.on_tick(bid, ask, timestamp)

        # Feed the self-configuring session selector (learns per-session movement)
        if self.session_tuner is not None:
            try:
                st = self.live_state._state
                sess = st.session if st else None
                pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
                self.session_tuner.update_tick(sess, (bid + ask) / 2.0, (ask - bid) / pip)
            except Exception as e:
                logger.debug("SessionTuner update failed: %s", e)

        # Manage trailing stops + detect closed positions (SL/TP). This drives
        # live trailing-stop edits, the post-trade cooldown, and the per-pair SL
        # lockout, so it MUST run in live mode too — gated behind a safety toggle
        # (default on) so the newly-live trailing behavior can be soaked in dry-run.
        if self.config.get("realtime_manage_positions_live", True):
            try:
                self._manage_trailing_stops(bid, ask)
                self._check_for_closed_positions(bid, ask)
            except Exception as e:
                logger.error("Error managing trailing stops / closed positions: %s", e, exc_info=True)
        
        # Broadcast tick to dashboard WebSocket.
        # On an execution node the detection telemetry below is meaningless (the
        # peak-detector fields never update because detection is off), and
        # _get_regime_payload recomputes an EMA + the session ranges every 5th
        # tick. Send only the account payload, which is what actually matters on
        # a prop account: equity, balance, and the drawdown headroom.
        dashboard = get_dashboard()
        if dashboard and self._exec_node:
            if self.tick_engine._tick_count % 5 == 1:
                acc_payload = self._get_account_payload()
                if acc_payload:
                    self._broadcast(acc_payload)
        elif dashboard:
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
                # 1. Check for tick efficiency collapse (Price is moving fast but not going anywhere)
                eff = getattr(self.event_detector.peak_detector, "_last_efficiency", 1.0)
                collapse = (eff < 0.15) and (velocity > 1.5)

                # 2. Check for aggression shift (Sudden reversal in order flow dominance)
                i60 = imb.get("imbalance_60s", 0.0)
                i10 = imb.get("imbalance_10s", 0.0)
                agg_shift = (i60 > 0.4 and i10 < -0.4) or (i60 < -0.4 and i10 > 0.4)

                # 3. Check for absorption (High volume, high velocity, but zero displacement)
                t_30s = [t for t in ticks if (ticks[-1]['time'] - t['time']).total_seconds() <= 30.0]
                pip_unit = 0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001
                absorption = len(t_30s) >= 20 and velocity > 1.5 and abs(t_30s[-1]['mid'] - t_30s[0]['mid']) < (2.0 * pip_unit)

            self._broadcast({
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
                self._broadcast(self._get_regime_payload())
                
                # Fetch and broadcast MetaTrader 5 account info
                acc_payload = self._get_account_payload()
                if acc_payload:
                    self._broadcast(acc_payload)

    def _on_candle_close(self, candle: LiveCandle):
        """Called by TickEngine when any timeframe candle closes."""
        self.event_detector.on_candle_close(candle)
        logger.debug("Candle closed: %s @ %.5f (H=%.5f L=%.5f)",
                     candle.timeframe, candle.close, candle.high, candle.low)

        # Execution node: no chart, no levels, no regime panel — but DO keep the
        # news calendar warm, because _check_pre_news_close runs natively here and
        # flattens this account's positions ahead of high-impact releases.
        if self._exec_node:
            if candle.timeframe in ("M15", "H1"):
                try:
                    self.news_guard.refresh()
                except Exception as ne:
                    logger.warning("Failed to refresh NewsGuard on candle close: %s", ne)
            return

        # Broadcast closed candle
        dashboard = get_dashboard()
        if dashboard:
            self._broadcast({
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
                self._broadcast(self._get_candles_payload(candle.timeframe))
                self._broadcast(self._get_levels_payload())
                self._broadcast(self._get_regime_payload())
                
                # Periodically refresh news calendar cache in the background
                try:
                    self.news_guard.refresh()
                except Exception as ne:
                    logger.warning("Failed to refresh NewsGuard on candle close: %s", ne)

    def _event_loop(self):
        """Main thread: blocks on event queue, fires graph on valid events."""
        import time as pytime
        last_stats_time = pytime.time()
        # An execution node is deliberately idle, so it reports every 60s rather
        # than every 10s (~8,600 lines/day → ~1,400).
        stats_interval = 60.0 if self._exec_node else 10.0
        while self._running:
            try:
                event = self.event_queue.get(timeout=1.0)
            except queue.Empty:
                if pytime.time() - last_stats_time > stats_interval:
                    self._log_stats()
                    last_stats_time = pytime.time()
                continue

            self._events_detected += 1

            # Execution-node mode: this terminal never acts on its OWN detected
            # signals — entries arrive only via inject_signal() from the lead
            # brain. Position management (trailing / EOD / exits) still runs
            # natively in _on_tick, so B has full order management of its trades.
            if self._exec_node:
                continue

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
                            # 3. Range Extreme Gate — reject entries at the WRONG END of the
                            #    real prior range: selling into support (bottom of the range)
                            #    or buying into resistance (top). See _range_extreme_gate.
                            passed, reason = self._range_extreme_gate(direction, event.price)
                            if not passed:
                                is_gate_passed = False
                                gate_reason = reason

                            if is_gate_passed:
                                logger.info("LIVE PEAK GATE: S/R Zone Proximity + Trend Aligned! Price=%.5f is %.2f pips from %s level %.5f. Trend=%s, Trade=%s",
                                            event.price, closest_dist, closest_lvl.level_type, closest_lvl.price, daily_trend, direction)

            dashboard = get_dashboard()
            if not self.config.get("test_mode", False) and not (is_peak and is_exhaustion and is_gate_passed):
                self._events_skipped += 1
                if dashboard:
                    self._broadcast({
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

            # Dynamic News Guard econ calendar check
            blocked, news_reason = self.news_guard.should_block_entry(self.mt5_symbol)
            if blocked:
                self._events_skipped += 1
                logger.info("ENTRY BLOCKED by News Guard: %s", news_reason)
                if dashboard:
                    self._broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": f"News Block: {news_reason}",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            dashboard = get_dashboard()
            if dashboard:
                self._broadcast({
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
            # NOTE: cooldown/priority gating is already enforced by EventDetector._emit
            # (set via event_detector.set_cooldown after each execution below), so no
            # separate execution-controller gate is needed here.

            # Snapshot current state
            ws = self.live_state.snapshot()
            me = self.live_evidence.snapshot()

            # Session Gate — self-configuring when realtime_auto_sessions is on
            # (the tuner learns which sessions this pair actually moves in);
            # otherwise the manual "realtime_active_sessions" list is used.
            active_sessions = self._current_active_sessions()
            if ws and ws.session not in active_sessions:
                self._events_skipped += 1
                logger.info("SKIPPED (session gate: current=%s not in %s)", ws.session, active_sessions)
                if dashboard:
                    self._broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": f"session '{ws.session}' not in active_sessions",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Per-pair SL lockout: bar new entries until the next trading day.
            if self._sl_locked_out:
                self._events_skipped += 1
                logger.info("SKIPPED (SL lockout active until new trading day)")
                if dashboard:
                    self._broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": "SL lockout (until new trading day)",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # EOD entry cutoff: bar NEW entries 23:00→06:00 IST (open trades are
            # still held + engine-managed; they flatten ~5 min before the rollover).
            if self._eod_flat_blocked:
                self._events_skipped += 1
                logger.info("SKIPPED (EOD entry cutoff 23:00-06:00 IST; no new entries)")
                if dashboard:
                    self._broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": "EOD entry cutoff (23:00-06:00 IST)",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            self._events_fired += 1
            dir_str = event.details.get("direction", "")
            if "bullish" in dir_str or "low" in peak_type:
                signal = "Buy"
            else:
                signal = "Sell"
            
            system_name = event.details.get("system", "optimized")
            logger.info("EXECUTION (%s): Direct signal: %s", system_name, signal)
            
            # Broadcast decision status
            if dashboard:
                self._broadcast({
                    "type": "decision",
                    "signal": signal,
                    "system": system_name,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
            
            # Correlation-engine gate: veto or size-scale a follower-pair entry
            # based on cross-pair net-USD exposure, the lead pair's bias, and
            # rolling correlation. The lead pair (EURUSD) is never gated.
            size_scale = 1.0
            if self.correlation_engine is not None:
                try:
                    allow, size_scale, corr_reason = self.correlation_engine.evaluate_entry(
                        self.mt5_symbol, signal, self.live_state, self.live_evidence)
                except Exception as ce:
                    allow, size_scale, corr_reason = True, 1.0, f"engine error: {ce}"
                if not allow:
                    self._events_skipped += 1
                    logger.info("SKIPPED (correlation: %s)", corr_reason)
                    self._broadcast({
                        "type": "event",
                        "id": self._events_detected,
                        "event_type": event.event_type.value,
                        "priority": event.priority.name,
                        "price": event.price,
                        "details": event.details,
                        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "skipped",
                        "reason": f"correlation: {corr_reason}",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                    continue

            # Execute order on MT5 terminal using the correct trade executor
            trade_result = None
            try:
                trade_result = self.trade_executor.execute_signal(self.mt5_symbol, signal, self.live_state, size_scale)
                if trade_result:
                    logger.info("AxonDaemon: Order execution complete for %s system: %s", system_name, trade_result)
                    ticket = trade_result.get("order")
                    if ticket:
                        self._tracked_positions.add(ticket)
                        self._active_trade_initial_sl[ticket] = trade_result.get("sl")
                        self._active_trade_system[ticket] = system_name
                        atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                        self._active_trade_atr[ticket] = atr
                        self._active_trade_peak_price[ticket] = trade_result.get("price", 0.0)
                        if self.correlation_engine is not None:
                            self.correlation_engine.register_position(
                                self.mt5_symbol, signal,
                                trade_result.get("volume", 0.0) or 0.0,
                                trade_result.get("price", 0.0) or 0.0, ticket)
                    # Mirror this entry decision to the execution node (best-effort;
                    # lead side only — a no-op when mirror_client is None).
                    self._mirror_send({"cmd": "enter", "signal": signal, "size_scale": size_scale})
            except Exception as ex_err:
                logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)
            
            # Persistently log signal to file
            self._log_signal(event, ws, signal, trade_result)
            
            # Set cooldown on event detector
            cooldown = self.config.get("realtime_cooldown_seconds", 300)
            self.event_detector.set_cooldown(cooldown)
            
            # Print stats
            self._log_stats()

    def _log_signal(self, event, ws, signal, trade_result):
        """Persistently log every generated signal to reports/signals.jsonl and reports/signals.log."""
        import json
        import os
        
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_payload = {
            "timestamp": timestamp_str,
            "system": event.details.get("system", "optimized"),
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
            with open(self._report_path("signals.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(log_payload) + "\n")
        except Exception as e:
            logger.error("Failed to append to signals.jsonl: %s", e)

        # Append to signals.log
        try:
            with open(self._report_path("signals.log"), "a", encoding="utf-8") as f:
                f.write(
                    f"[{timestamp_str}] TICKER: {self.yf_symbol} | EVENT: {event.event_type.value} ({event.priority.name}) "
                    f"| REGIME: {ws.dominant_regime} ({ws.regime_confidence:.2f}) | DECISION: {signal} "
                    f"| RESULT: {trade_result}\n"
                )
        except Exception as e:
            logger.error("Failed to append to signals.log: %s", e)

    def _check_eod_close(self, bid: float, ask: float) -> None:
        """Flatten all positions on the active → wind-down session transition if they are in profit."""
        if not self.config.get("eod_close_enabled", True):
            return
        state = getattr(self.live_state, "_state", None)
        current = getattr(state, "session", None) if state is not None else None
        if current is None:
            return
        prev = self._last_session
        self._last_session = current

        # Guard: only check for transitions if we have a previous known session
        if prev is None or prev == current:
            return

        active = set(self.config.get("eod_close_active_sessions", ["london", "overlap", "newyork"]))
        trigger = set(self.config.get("eod_close_trigger_sessions", ["rollover", "asian"]))

        if prev in active and current in trigger:
            logger.info("AxonDaemon: EOD transition %s → %s; flattening profitable positions", prev, current)
            self._close_all_profitable_positions(bid, ask, "End of Day (Session Close)")

    def _trading_day(self, now_utc: datetime):
        """Calendar date of the current trading day.

        The trading day is anchored to the morning resume boundary
        (``eod_resume_utc``, 00:30 UTC = 06:00 IST): a tick before 06:00 IST
        still belongs to the previous trading day. The per-pair SL lockout and
        the once-a-night pre-rollover flatten both key off this, so "until the
        next trading day" means "until the 06:00 IST session reset" — a stop-out
        in the overnight hold window no longer bars the pair through the next day.
        """
        resume = float(self.config.get("eod_resume_utc", 0.5))
        utc_hour = now_utc.hour + now_utc.minute / 60.0
        d = now_utc.date()
        if utc_hour < resume:
            d = d - timedelta(days=1)
        return d

    def _check_daily_reset(self, now_utc: datetime) -> None:
        """Clear per-day guards at the 06:00 IST session reset (trading-day roll)."""
        td = self._trading_day(now_utc)
        if self._last_trading_day is None:
            self._last_trading_day = td
            return
        if td != self._last_trading_day:
            self._last_trading_day = td
            # The nightly EOD blackout (_check_eod_hard_flat) now owns
            # _eod_flat_blocked on its own fixed-UTC schedule, so only the
            # per-pair SL lockout is cleared on the trading-day roll.
            if self._sl_locked_out:
                logger.info("Daily reset: new trading day %s; clearing SL lockout", td)
            self._sl_locked_out = False

    def _maybe_engage_sl_lockout(self, reason: str, pips: float = 0.0) -> None:
        """Engage the per-pair SL lockout only on a genuine *losing* stop-out.

        The exit reason alone is NOT sufficient: a trailing stop that has been
        moved into profit is still reported by the broker with an "sl" comment,
        so it gets the same "Stop Loss (SL) Hit" label as a real loss (the
        classifier only tags "Trailing SL Hit" when the exit sits near
        breakeven). In practice most "Stop Loss (SL) Hit" exits are actually
        profitable trailed stops, so keying the lockout on the label alone
        barred the pair after winning trades. Gate on the actual outcome:
        price must have moved against the entry (``pips < 0``).

        Trailing/TP/manual closes and any profitable stop do NOT lock out.
        Cleared by ``_check_daily_reset`` when the trading day rolls.
        """
        if reason in ("Stop Loss (SL) Hit", "Stop Out (SO)") and pips < 0:
            if not self._sl_locked_out:
                logger.info(
                    "SL lockout ENGAGED for %s (%s, %.1f pips loss); no new entries until the next trading day",
                    self.mt5_symbol, reason, pips,
                )
            self._sl_locked_out = True

    def _check_eod_hard_flat(self, now_utc: datetime) -> None:
        """End-of-day handling: entry cutoff, hold, pre-rollover flatten, resume.

        Three fixed points each trading day (all UTC; the no-new-entry window
        wraps past midnight):
          * ``eod_entry_cutoff_utc`` (23:00 IST): stop opening NEW positions.
            Open trades are HELD and left to the engine's own exits
            (trailing/SL/TP) — nothing is force-closed here, so a losing trade
            keeps its chance to recover into the daily close.
          * ``eod_flatten_before_close_min`` before the NY 5pm daily rollover
            (DST-aware: ny_close + 3h, ~20:55 UTC = ~02:25 IST): force-flat ALL
            remaining positions once, so nothing is carried into the close.
          * ``eod_resume_utc`` (06:00 IST): the block lifts and the trading day
            resets (see ``_trading_day``), so new entries resume as normal.

        This method is the sole runtime owner of ``_eod_flat_blocked``.
        """
        if not self.config.get("eod_hard_flat_enabled", True):
            self._eod_flat_blocked = False
            return

        cutoff = float(self.config.get("eod_entry_cutoff_utc", 17.5))   # 23:00 IST
        resume = float(self.config.get("eod_resume_utc", 0.5))          # 06:00 IST
        utc_hour = now_utc.hour + now_utc.minute / 60.0

        # 1) No-new-entry window [cutoff, resume), wrapping past midnight UTC.
        #    Positions stay open and engine-managed; only NEW entries are barred.
        self._eod_flat_blocked = (utc_hour >= cutoff) or (utc_hour < resume)

        # 2) Pre-rollover flatten: force-close ALL remaining positions once, a few
        #    minutes before the NY 5pm daily rollover (ny_close + 3h, DST-aware).
        _, _, _, ny_close = get_dst_session_hours(now_utc)
        rollover = (ny_close + 3.0) % 24.0
        before_min = float(self.config.get("eod_flatten_before_close_min", 5))
        flat_after = (rollover - before_min / 60.0) % 24.0             # ~20:55 UTC / 02:25 IST

        # Flatten window [flat_after, resume), wrapping past midnight UTC. Keyed to
        # the trading day so it fires exactly once per night (and again after a
        # mid-window restart, which re-adopts and then flattens open positions).
        if (utc_hour >= flat_after) or (utc_hour < resume):
            td = self._trading_day(now_utc)
            if td != self._eod_flat_tradeday:
                closed = self._close_all_positions("EOD Flat (pre-rollover)")
                self._eod_flat_tradeday = td
                logger.info(
                    "AxonDaemon: EOD pre-rollover flat at %.2fh UTC (rollover=%.2fh, ~02:25 IST) — "
                    "force-closed %d position(s); entries stay blocked until %.2fh UTC (06:00 IST)",
                    utc_hour, rollover, closed, resume,
                )

    def _check_pre_news_close(self, bid: float, ask: float) -> None:
        """Flatten ALL open positions (profit or loss) within 5 minutes before a
        high-impact news event."""
        if not self.config.get("news_guard_enabled", True):
            return

        pre_close_minutes = float(self.config.get("news_guard_pre_close_minutes", 5))
        now_utc = datetime.now(timezone.utc)
        ccys = self.news_guard._currencies_for(self.mt5_symbol)
        if not ccys:
            return

        for event in self.news_guard._events:
            dt = event["dt"]
            currency = event["currency"]
            impact = event["impact"]
            title = event["title"]
            if currency not in ccys:
                continue
            if impact.lower() not in self.news_guard.block_impacts:
                continue

            # Fire only inside the tight pre-event window (default 5 min before).
            mins_to_event = (dt - now_utc).total_seconds() / 60.0
            if 0 <= mins_to_event <= pre_close_minutes:
                closed = self._close_all_positions(
                    f"Pre-News Close ({impact} {currency} news '{title}')"
                )
                if closed > 0:
                    logger.info(
                        "NewsGuard: Flattened %d positions %.1fm before news event: %s",
                        closed, mins_to_event, title,
                    )
                break

    def _close_all_positions(self, reason: str) -> int:
        """Close every open position for this symbol/magic, regardless of PnL.

        Used by the pre-news flatten and the manual dashboard close-all button.
        Fetches the current tick internally so callers need not supply bid/ask.
        """
        if not mt5 or not mt5.terminal_info():
            logger.warning("Close-all: MT5 not connected, cannot close positions.")
            return 0

        positions = mt5.positions_get(symbol=self.mt5_symbol)
        if not positions:
            return 0

        tick = mt5.symbol_info_tick(self.mt5_symbol)
        if not tick:
            logger.warning("Close-all: no tick for %s, cannot close.", self.mt5_symbol)
            return 0
        bid, ask = tick.bid, tick.ask

        closed_count = 0
        for pos in positions:
            if pos.magic != self.trade_executor_opt.magic:
                continue

            if pos.type == mt5.POSITION_TYPE_BUY:
                close_price = bid
                close_type = mt5.POSITION_TYPE_SELL
            else:
                close_price = ask
                close_type = mt5.POSITION_TYPE_BUY

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": close_price,
                "deviation": 20,
                "magic": pos.magic,
                "comment": reason[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            with mt5_lock:
                res = mt5.order_send(request)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info("Close-all: closed position %d (%.2f profit) — %s",
                            pos.ticket, pos.profit, reason)
                closed_count += 1
            else:
                logger.warning("Close-all: failed to close position %d: %s",
                               pos.ticket, getattr(res, "comment", "Unknown"))

        return closed_count

    def _close_all_profitable_positions(self, bid: float, ask: float, reason: str) -> int:
        """Close open positions for this symbol/magic ONLY if they are currently in profit."""
        if not mt5 or not mt5.terminal_info():
            logger.warning("EOD Close: MT5 not connected, cannot close positions.")
            return 0

        positions = mt5.positions_get(symbol=self.mt5_symbol)
        if not positions:
            return 0

        closed_count = 0
        pip = 0.01 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 0.0001

        for pos in positions:
            # Verify magic number matches our optimized strategy executor
            if pos.magic != self.trade_executor_opt.magic:
                continue

            # Calculate profit pips
            if pos.type == mt5.POSITION_TYPE_BUY:
                profit_pips = (bid - pos.price_open) / pip
                close_price = bid
            else:
                profit_pips = (pos.price_open - ask) / pip
                close_price = ask

            # ONLY close if in profit!
            if profit_pips > 0:
                logger.info("EOD: Position %d is in profit (+%.1f pips). Force closing...", pos.ticket, profit_pips)
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": pos.symbol,
                    "volume": pos.volume,
                    "type": mt5.POSITION_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.POSITION_TYPE_BUY,
                    "position": pos.ticket,
                    "price": close_price,
                    "deviation": 20,
                    "magic": pos.magic,
                    "comment": f"EOD profit close"[:31],
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                
                with mt5_lock:
                    res = mt5.order_send(request)
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info("EOD: Successfully closed profitable position %d (+%.2f profit)", pos.ticket, pos.profit)
                    closed_count += 1
                else:
                    logger.warning("EOD: Failed to close position %d: %s", pos.ticket, getattr(res, "comment", "Unknown"))
            else:
                logger.info("EOD: Position %d is in loss (%.1f pips). Leaving open.", pos.ticket, profit_pips)

        return closed_count

    # ── prop-firm compliance ──────────────────────────────────────────────────
    def _check_prop_risk(self) -> None:
        """Feed live equity to the risk guard and flatten if a limit is breached.

        No-op unless ``prop_guard_enabled`` is set on THIS process's config, so a
        non-prop account is completely unaffected.

        Two jobs the plain breaker cannot do on its own:
          * ``update_equity`` normally only runs when a signal fires, so a
            floating loss could sail past the limit unseen between signals. Here
            it is refreshed on a timer from live account info.
          * On breach, open positions are CLOSED — blocking new entries alone
            does not stop an open trade from breaching the account.
        """
        rg = getattr(self.trade_executor_opt, "risk_guard", None)
        if rg is None or not getattr(rg, "prop_enabled", False):
            return
        if not mt5 or not mt5.terminal_info():
            return

        import time as pytime
        now = pytime.time()
        # Account info is a terminal round-trip; a few times a second is plenty.
        if now - getattr(self, "_last_prop_check", 0.0) < 2.0:
            return
        self._last_prop_check = now

        acc = mt5.account_info()
        if not acc:
            return
        rg.update_equity(acc.equity, acc.balance)

        halted, reason = rg.is_halted(acc.equity)
        if not halted:
            self._prop_flattened = False
            return

        if not getattr(rg, "flatten_on_breach", True) or self._prop_flattened:
            return
        # A breach usually happens during a violent move — exactly when a close
        # can be requoted, hit off-quotes, or find the terminal briefly gone. So
        # the "flattened" latch is set ONLY after open positions are verified
        # gone; otherwise this retries on the next check rather than believing a
        # failed close succeeded and leaving a losing position running.
        logger.critical("=" * 60)
        logger.critical("PROP RISK BREACH — flattening %s: %s", self.mt5_symbol, reason)
        logger.critical("=" * 60)
        try:
            closed = self._close_all_positions("Risk limit breach")
        except Exception as e:
            logger.error("Prop risk breach: flatten attempt failed on %s: %s",
                         self.mt5_symbol, e, exc_info=True)
            closed = 0

        remaining = self._open_position_count()
        if remaining != 0:
            # >0 = still open; -1 = could not verify. Both mean "not proven flat".
            logger.critical(
                "Prop risk breach: %s NOT verified flat after closing %d "
                "(remaining=%s) — will retry on next check",
                self.mt5_symbol, closed, "unknown" if remaining < 0 else remaining)
            return  # leave _prop_flattened False so the next tick retries

        self._prop_flattened = True
        logger.critical("Prop risk breach: %s is flat (closed %d position(s)); entries halted.",
                        self.mt5_symbol, closed)
        try:
            send_alert(f"PROP RISK BREACH on {self.mt5_symbol}: {reason} "
                       f"— closed {closed} position(s); entries halted.", self.config)
        except Exception as e:
            logger.error("Prop risk breach: alert failed: %s", e)

    def _open_position_count(self) -> int:
        """Open positions for this symbol belonging to this strategy's magic."""
        try:
            if not mt5 or not mt5.terminal_info():
                return -1  # unknown; treated as "not verified flat"
            positions = mt5.positions_get(symbol=self.mt5_symbol)
            if not positions:
                return 0
            return sum(1 for p in positions if p.magic == self.trade_executor_opt.magic)
        except Exception as e:
            logger.warning("Prop risk: could not verify open positions: %s", e)
            return -1

    def mirror_position_state(self) -> dict:
        """This symbol's live position summary, for mirror reconcile.

        Three DISTINCT outcomes, because "flat" and "could not read the terminal"
        must never collapse into one value: reconcile closes orphans, so an
        unreadable terminal reported as flat would make the other side's position
        look like a divergence — and close a perfectly good trade.

        * ``{"ok": False}``                            → not verified, act on nothing
        * ``{"ok": True, "signal": None}``             → verified flat
        * ``{"ok": True, "signal": "Buy", "count": n}``→ open

        Note ``positions_get`` returns ``()`` when flat but ``None`` on error;
        those are separated here (``_open_position_count`` folds both to 0).
        """
        try:
            if not mt5 or not mt5.terminal_info():
                return {"ok": False}
            positions = mt5.positions_get(symbol=self.mt5_symbol)
            if positions is None:
                return {"ok": False}
            mine = [p for p in positions if p.magic == self.trade_executor_opt.magic]
            if not mine:
                return {"ok": True, "signal": None}
            return {
                "ok": True,
                "signal": "Buy" if mine[0].type == mt5.POSITION_TYPE_BUY else "Sell",
                "count": len(mine),
            }
        except Exception as e:
            logger.warning("mirror_position_state: could not read positions: %s", e)
            return {"ok": False}

    # ── A→B order mirror ──────────────────────────────────────────────────────
    def _apply_exec_node_overrides(self) -> None:
        """Follower terminal: re-size to THIS account with a distinct magic + cap.

        ``executor.config`` IS ``self.config`` (same dict), so bounding
        ``realtime_max_lot`` here directly caps the executor's sizing.
        """
        ml = self.config.get("exec_node_max_lot")
        if ml:
            self.config["realtime_max_lot"] = float(ml)
        off = int(self.config.get("exec_node_magic_offset", 0) or 0)
        if off:
            self.trade_executor_opt.magic += off
            self.config["realtime_magic_number"] = self.trade_executor_opt.magic
        logger.info(
            "AxonDaemon[%s]: EXECUTION-NODE mode — magic=%d, max_lot=%s (self-detection OFF)",
            self.mt5_symbol, self.trade_executor_opt.magic,
            self.config.get("realtime_max_lot"),
        )

    def _mirror_send(self, payload: dict) -> None:
        """Forward a trade DECISION to the execution node (lead side, best-effort).

        Never raises and never blocks the trading path: if the mirror is disabled
        (``mirror_client is None``) or the node is unreachable, the decision is
        just logged as not mirrored. Only the decision crosses the wire — never a
        price — so a different-broker node re-derives ticker/pip/SL/TP/size itself.
        """
        mc = self.mirror_client
        if mc is None:
            return
        try:
            payload.setdefault("symbol", _canonical_symbol(self.mt5_symbol))
            mc.send(payload)
        except Exception as e:
            logger.warning("AxonDaemon: mirror forward failed (non-fatal): %s", e)

    def inject_signal(self, signal: str, size_scale: float = 1.0, source: str = "mirror"):
        """Execute an entry routed from the lead brain (execution-node mode).

        Bypasses detection + the entry gauntlet — the lead already applied every
        gate — and runs the engine's OWN order management (``execute_signal``),
        which sizes from THIS terminal's equity and resolves THIS broker's
        ticker/pip. Tracks the resulting position so the daemon's native trailing
        / EOD / exit management picks it up. Returns the executor result or None.
        """
        if signal not in ("Buy", "Sell", "Overweight", "Underweight"):
            logger.info("inject_signal: ignoring non-entry signal %r", signal)
            return None
        # getattr-guarded: the mirror tests exercise inject_signal on a daemon
        # built without __init__, and a telemetry counter must never be the thing
        # that stops a routed order from reaching the broker.
        self._orders_routed = getattr(self, "_orders_routed", 0) + 1
        trade_result = None
        try:
            trade_result = self.trade_executor.execute_signal(
                self.mt5_symbol, signal, self.live_state, size_scale)
            if trade_result:
                ticket = trade_result.get("order")
                if ticket:
                    self._tracked_positions.add(ticket)
                    self._active_trade_initial_sl[ticket] = trade_result.get("sl")
                    self._active_trade_system[ticket] = source
                    atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                    self._active_trade_atr[ticket] = atr
                    self._active_trade_peak_price[ticket] = trade_result.get("price", 0.0)
                    if self.correlation_engine is not None:
                        self.correlation_engine.register_position(
                            self.mt5_symbol, signal,
                            trade_result.get("volume", 0.0) or 0.0,
                            trade_result.get("price", 0.0) or 0.0, ticket)
                logger.info(
                    "inject_signal: executed %s on %s (scale %.2f) → ticket %s vol %s",
                    signal, self.mt5_symbol, size_scale,
                    trade_result.get("order"), trade_result.get("volume"))
            else:
                logger.info(
                    "inject_signal: %s on %s produced no fill (position open or rejected)",
                    signal, self.mt5_symbol)
        except Exception as e:
            logger.error("inject_signal: execution error: %s", e, exc_info=True)
        return trade_result

    def inject_close(self, reason: str = "mirror close") -> int:
        """Force-flatten this symbol's positions on request from the lead brain."""
        try:
            return self._close_all_positions(reason)
        except Exception as e:
            logger.error("inject_close: error: %s", e, exc_info=True)
            return 0

    def _log_stats(self):
        """Log daemon statistics."""
        if not self._start_time:
            return
        uptime = str(datetime.now() - self._start_time).split(".")[0]

        if self._exec_node:
            # A follower detects nothing and fires nothing, so the lead's STATS
            # line would read "events_detected=N | events_fired=0 | skipped=0"
            # forever — noise that reads as if the node were making decisions.
            # Report what this process actually does; it is meant to sit idle.
            logger.info(
                "EXEC-NODE[%s]: uptime=%s | ticks=%d | orders_routed=%d | open=%d",
                self.mt5_symbol, uptime, self.tick_engine._tick_count,
                self._orders_routed, len(self._tracked_positions),
            )
            return

        cooldown_rem = max(0.0, (self.event_detector._cooldown_until - datetime.now(timezone.utc if self.event_detector._cooldown_until.tzinfo else None)).total_seconds()) if hasattr(self.event_detector, "_cooldown_until") else 0.0
        logger.info(
            "STATS: uptime=%s | ticks=%d | events_detected=%d | "
            "events_fired=%d | events_skipped=%d | cooldown_remaining=%.0fs",
            uptime,
            self.tick_engine._tick_count,
            self._events_detected,
            self._events_fired,
            self._events_skipped,
            cooldown_rem,
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
        # The MT5 connection is process-wide and shared across pairs; only tear
        # it down when standalone. Under a supervisor, the supervisor owns it.
        if getattr(self, "supervisor", None) is None:
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
        # Tagged per instance: today only the lead reaches this call (the exec
        # node short-circuits _event_loop earlier), but the path itself is shared
        # and run.py sets realtime_dry_run=True for BOTH processes — so a single
        # new call site above that short-circuit would make it a two-writer race.
        log_path = self._report_path('dry_run_session.jsonl')

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

    def _range_extreme_gate(self, direction: str, price: float):
        """Reject wrong-end entries against the REAL prior range.

        A reversal system must sell into resistance (top of range) and buy into
        support (bottom) — never the opposite. Returns ``(passed, reason)``.

        The range is the prior ``range_gate_lookback`` *closed* M15 candles from
        ``live_evidence._m15_candles`` (seeded from ~10 days of history at init,
        so it is valid immediately after a restart — unlike the tick-engine
        builder history, which starts empty). A SELL must sit at ``>= 1-edge`` of
        that range, a BUY at ``<= edge``. There is deliberately NO silent bypass:
        the old gate measured only the currently-forming M15 candle and skipped
        entirely when its range was 0, which let SELLs fire deep at support.
        """
        lookback = int(self.config.get("range_gate_lookback", 20))
        edge = float(self.config.get("range_gate_edge", 0.25))
        m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-lookback:]
        if len(m15) < max(5, lookback // 2):
            # Insufficient history (seeding failed / brand-new symbol): fail SAFE
            # by refusing the entry rather than firing blind. Skipping a trade
            # never loses money; a blind wrong-end entry does.
            return False, f"range gate: insufficient M15 history ({len(m15)} candles)"
        rng_hi = max(c.high for c in m15)
        rng_lo = min(c.low for c in m15)
        rng = rng_hi - rng_lo
        if rng <= 0:
            # Degenerate range (every candle identical): no extreme to test.
            return True, ""
        # rel: 0.0 = bottom of range (support), 1.0 = top (resistance). Block only
        # WRONG-END entries — a SELL in the lower ``edge`` fraction (selling into
        # support) or a BUY in the upper ``edge`` fraction (buying into
        # resistance). Mid-range and correct-end entries pass untouched.
        rel = (price - rng_lo) / rng
        if direction == "SELL" and rel < edge:
            return False, f"SELL at/near support: pos {rel:.2f} of {lookback}xM15 range (need >= {edge:.2f})"
        if direction == "BUY" and rel > (1.0 - edge):
            return False, f"BUY at/near resistance: pos {rel:.2f} of {lookback}xM15 range (need <= {1.0 - edge:.2f})"
        logger.info("RANGE EXTREME GATE PASSED: %s at pos %.2f of %dxM15 range [%.5f-%.5f]",
                    direction, rel, lookback, rng_lo, rng_hi)
        return True, ""

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
                atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                self._active_trade_atr[ticket] = atr
                self._active_trade_peak_price[ticket] = pos.price_open
                
            # Ensure ATR and peak price are recorded
            if ticket not in self._active_trade_atr:
                atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                self._active_trade_atr[ticket] = atr
            if ticket not in self._active_trade_peak_price:
                self._active_trade_peak_price[ticket] = pos.price_open
                
            atr = self._active_trade_atr[ticket]
            initial_sl = self._active_trade_initial_sl[ticket]
            
            if initial_sl <= 0.0:
                continue
                
            if pos.type == mt5.POSITION_TYPE_BUY:
                # Update peak price using the bid price
                self._active_trade_peak_price[ticket] = max(self._active_trade_peak_price[ticket], bid)
                peak_price = self._active_trade_peak_price[ticket]
                
                # Check Breakeven Trigger (0.60 * ATR)
                profit = bid - pos.price_open
                be_trigger = 0.60 * atr
                
                # Check Trailing Stop (once peak profit >= 0.80 * ATR, trail by 0.35 * ATR)
                peak_profit = peak_price - pos.price_open
                trail_trigger = 0.80 * atr
                
                target_sl = pos.sl
                if profit >= be_trigger:
                    breakeven_sl = round(pos.price_open + 1 * pip, digits)
                    if target_sl < breakeven_sl:
                        target_sl = breakeven_sl
                        
                if peak_profit >= trail_trigger:
                    trail_distance = 0.35 * atr
                    trail_sl = round(peak_price - trail_distance, digits)
                    if target_sl < trail_sl:
                        target_sl = trail_sl
                
                if pos.sl < target_sl:
                    logger.info("AxonDaemon: Trailing SL triggered for BUY ticket %d. Modifying SL: %.5f -> %.5f",
                                ticket, pos.sl, target_sl)
                    self._modify_sl(ticket, target_sl, pos.tp, "BUY")

            elif pos.type == mt5.POSITION_TYPE_SELL:
                # Update peak price using the ask price
                self._active_trade_peak_price[ticket] = min(self._active_trade_peak_price[ticket], ask)
                peak_price = self._active_trade_peak_price[ticket]
                
                # Check Breakeven Trigger (0.60 * ATR)
                profit = pos.price_open - ask
                be_trigger = 0.60 * atr
                
                # Check Trailing Stop (once peak profit >= 0.80 * ATR, trail by 0.35 * ATR)
                peak_profit = pos.price_open - peak_price
                trail_trigger = 0.80 * atr
                
                target_sl = pos.sl
                if profit >= be_trigger:
                    breakeven_sl = round(pos.price_open - 1 * pip, digits)
                    if target_sl > breakeven_sl or target_sl == 0.0:
                        target_sl = breakeven_sl
                        
                if peak_profit >= trail_trigger:
                    trail_distance = 0.35 * atr
                    trail_sl = round(peak_price + trail_distance, digits)
                    if target_sl > trail_sl or target_sl == 0.0:
                        target_sl = trail_sl
                        
                if pos.sl > target_sl or pos.sl == 0.0:
                    logger.info("AxonDaemon: Trailing SL triggered for SELL ticket %d. Modifying SL: %.5f -> %.5f",
                                ticket, pos.sl, target_sl)
                    self._modify_sl(ticket, target_sl, pos.tp, "SELL")

    def _modify_sl(self, ticket: int, target_sl: float, tp: float, side: str) -> bool:
        """Send a lock-serialized SLTP modify; log + alert (throttled) on failure.

        The old code only logged on SUCCESS, so a broker-rejected trail update
        (requote, off-quotes, momentary disconnect) was swallowed silently and
        the stop never advanced without anyone knowing. Now a failure is logged
        and alerted, both throttled to once per 60s per ticket. The trailing loop
        re-evaluates every tick, so a transient rejection is naturally retried on
        the next pass — which is also why the log has to be throttled: an
        un-throttled warning produced one line per tick for as long as the broker
        kept rejecting, which is loud enough to bury every other log line.
        """
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": self.mt5_symbol,
            "sl": target_sl,
            "tp": tp,
        }
        with mt5_lock:
            res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("AxonDaemon: Modify SL successful for %s ticket %d -> %.5f", side, ticket, target_sl)
            self._sl_fail_alert_ts.pop(ticket, None)
            return True
        rc = getattr(res, "retcode", None)
        cm = getattr(res, "comment", "no result")
        now = time.time()
        due = now - self._sl_fail_alert_ts.get(ticket, 0.0) > 60.0
        if due:
            logger.warning("AxonDaemon: Modify SL FAILED for %s ticket %d (retcode=%s %s); "
                           "SL stays at broker value, retrying every tick "
                           "(further failures logged at most once per 60s)",
                           side, ticket, rc, cm)
        else:
            logger.debug("AxonDaemon: Modify SL still failing for %s ticket %d (retcode=%s %s)",
                         side, ticket, rc, cm)
        if due:
            self._sl_fail_alert_ts[ticket] = now
            try:
                send_alert(f"SL trail modify FAILED on {self.mt5_symbol} {side} ticket {ticket} "
                           f"(retcode={rc} {cm}) — stop not advanced.", self.config)
            except Exception as e:
                logger.error("SL-modify alert failed: %s", e)
        return False

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

            # Per-pair SL lockout (F4): engage only on a real *losing* stop-out
            # (pips < 0), never on a profitable trailed stop wearing the same label.
            self._maybe_engage_sl_lockout(reason, pips)
            # Mirror the exit to the execution node so the follower flattens the
            # matching position too (best-effort; idempotent if B is already flat).
            self._mirror_send({"cmd": "close", "reason": reason, "ticket": ticket})
            if self.correlation_engine is not None:
                self.correlation_engine.unregister_position(ticket)

            outcome = "WIN" if pips > 0 else "LOSS" if pips < 0 else "BREAKEVEN"
            
            system_name = self._active_trade_system.pop(ticket, "optimized")
            
            # Log outcome to file
            log_msg = f"TRADE CLOSED: Ticket {ticket} | System: {system_name} | {direction} | Entry: {entry_price:.5f} | Exit: {exit_price:.5f} | Profit: {profit:+.2f} | Pips: {pips:+.1f} | Reason: {reason} | Outcome: {outcome}"
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
                    "system": system_name,
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
                with open(self._report_path("signals.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")

                with open(self._report_path("signals.log"), "a", encoding="utf-8") as f:
                    f.write(f"[{exit_time_str}] {log_msg}\n")
            except Exception as le:
                logger.error("Failed to write closed position log: %s", le)
                
            # Broadcast to dashboard
            dashboard = get_dashboard()
            if dashboard:
                self._broadcast({
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
            self._active_trade_atr.pop(ticket, None)
            self._active_trade_peak_price.pop(ticket, None)
            
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
