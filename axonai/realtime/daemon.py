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
import threading
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
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.sessions import get_dst_session_hours, session_hud
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
        self._active_trade_sl: dict[int, float] = {}  # ticket -> last SL we CONFIRMED on the broker (ratchet shadow; not the flaky pos.sl read)
        self._active_trade_system: dict[int, str] = {}
        self._active_trade_atr: dict[int, float] = {}
        self._active_trade_peak_price: dict[int, float] = {}
        self._active_trade_worst_price: dict[int, float] = {}  # ticket -> worst ADVERSE price seen (for MAE)
        self._retest_arm: dict[int, dict] = {}  # ticket -> retest-confirmation verdict state (shadow/veto)
        self._stage_arm: dict[int, dict] = {}  # probe ticket -> staged-entry add state (confirmation-by-degree)
        self._mfe_exit_shadow: dict[int, dict] = {}  # ticket -> MFE early-exit shadow state (first-fire snapshot)
        self._exit_shadow: dict[int, dict] = {}  # ticket -> alt-trail exit-capture shadow (first shadow-stop hit)
        self._active_trade_sr_level: dict[int, float] = {}  # ticket -> faded S/R level @ entry (shadow-cut observer)
        self._shadow_cut = None  # lazily-built ShadowCutTracker (research/, lead-only, flag-gated OFF)
        self._brk_setups: list = []  # active break-and-retest continuation-shadow state machines
        self._wtms_setups: list = []  # wide-TP MTF-regime shadow: virtual fade+with-trend brackets (read-only)
        self._revconf_watches: list = []  # active reversal-confirmation PRE-ENTRY shadow watches
        self._retest_setup: Optional[dict] = None  # armed structure-shift retest entry setup (lead-only)
        self._retest_fwd: list = []  # structure-retest forward fixed-rail P&L shadow trackers
        self._retest_last_fire_ts: float = 0.0  # cooldown anchor for structure-retest REAL fires
        self._retest_last_log_ts: float = 0.0  # dedup anchor for structure-retest SHADOW logs (churn cap)
        self._retest_shift_cache: Optional[dict] = None  # cached shift-zigzag (~30s)
        self._regime_map_cache: Optional[dict] = None  # cached per-TF regime map core (~regime_map_cache_sec)
        self._entry_lock = threading.Lock()  # serializes the two entry paths (exhaustion event-thread
        #                                       vs structure-retest tick-thread) so they can never both
        #                                       open a position on this symbol+magic (1-per-magic race).
        self._active_trade_entry_time: dict[int, float] = {}  # ticket -> wall-clock entry epoch (for no-progress abort)
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
        self._daily_loss_usd = 0.0          # cumulative realized loss this trading day (daily-loss-cap mode)
        self._mtf_cache = None              # (epoch, stamp) — ~60s cache for the MTF entry stamp

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
        """Human dashboard session bars (Sydney/Tokyo/London/New York) with
        active-state + progress. Delegates to the single session source
        (axonai/sessions.session_hud) so there is one DST computation."""
        return session_hud(now_utc)

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
        session_details = self._get_session_details(now_utc)

        # Session-range windows from the single source (axonai/sessions.py). NY
        # uses the analytic close (~12–18 UTC summer), which aligns the dashboard
        # NY range with the NY levels/classifier — this copy previously ran to
        # 16:00 ET (12–20), the only place that disagreed.
        ldn_open, ldn_close, ny_open, ny_close = get_dst_session_hours(now_utc)

        # Real-time session ranges update using latest tick price
        current_bid = self.tick_engine.latest_bid
        if current_bid > 0.0:
            utc_hour = now_utc.hour + now_utc.minute / 60.0
            if 0 <= utc_hour < ldn_open:
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

        # REVP-confluence study: accumulate the intra-M15-bar exhaustion extremes
        # (velocity_divergence ~ reversal pressure; price_per_tick_efficiency ~
        # displacement). LEAD only -- the detector telemetry is inert on the exec
        # node. Two float compares per tick; fully fail-safe.
        if not self._exec_node and self.config.get("revp_telemetry_log", True):
            try:
                _pd = self.event_detector.peak_detector
                _d = float(getattr(_pd, "_last_divergence", 0.0) or 0.0)
                _e = float(getattr(_pd, "_last_efficiency", 1.0) or 1.0)
                if _d > getattr(self, "_revp_div_max", 0.0):
                    self._revp_div_max = _d
                if _e < getattr(self, "_revp_eff_min", 1.0):
                    self._revp_eff_min = _e
            except Exception:
                pass

        # Reversal-confirmation PRE-ENTRY shadow: advance pending would-wait watches
        # on this tick (LEAD only; pure observation, never trades). Fail-safe.
        if not self._exec_node and self.config.get("revconf_shadow", True) and self._revconf_watches:
            try:
                self._update_revconf_shadow(bid, ask)
            except Exception:
                pass

        # Structure-shift RETEST entry (LEAD only): a WITH-trend trigger the exhaustion
        # fader lacks. Detects a confirmed shift, arms the broken swing level, and fires
        # on a retest+rejection — shadow-logs every would-fire, places a real order only
        # when structure_retest_enabled. Fully fail-safe (an entry-trigger bug must never
        # interrupt tick processing). See _maybe_fire_structure_retest.
        if not self._exec_node and self.config.get("structure_retest_shadow", True):
            try:
                _rpip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
                self._maybe_fire_structure_retest(bid, ask, _rpip)
                self._update_retest_fwd(bid, ask, _rpip)
            except Exception:
                pass

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

        # REVP-confluence study: one exhaustion-telemetry row per closed M15 bar
        # (LEAD only; the exec-node path already returned above). Fail-safe.
        if candle.timeframe == "M15" and self.config.get("revp_telemetry_log", True):
            self._log_revp_telemetry(candle)

        # Break-and-retest continuation shadow (Stage-1 + lifecycle; LEAD only; never
        # trades). Runs on M15 closes to detect breakouts, track the retest/confirm
        # state machine, and log forward outcomes for offline edge validation.
        if candle.timeframe == "M15" and self.config.get("breakout_retest_shadow", True):
            self._update_breakout_retest_shadow(candle)

        # Wide-TP MTF-regime shadow (2026-09-01): resolve the virtual fade + with-trend
        # wide brackets armed on each gated fade signal, to ~5-day / weekend horizon.
        # READ-ONLY; feeds the forward good-spot map (research/mtf_regime_switch/).
        if candle.timeframe == "M15" and self.config.get("wide_tp_mtf_shadow_enabled", True):
            self._update_wtms_shadow(candle)

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

                    # 1a. Direction-aware S/R selection (config-gated, per-pair via
                    #     SYMBOL_CALIBRATION["direction_aware_sr"]). A fade should lean on a
                    #     level in its PROFIT direction: a SELL fades OFF resistance at/above
                    #     price, a BUY fades OFF support at/below. Selling INTO support-below
                    #     (or buying INTO resistance-above) is the "wrong-side" fade the
                    #     249-trade log study flagged: net loser on EURUSD (PF 0.80) but a
                    #     WINNER on USDJPY (PF 2.54, levels break) — hence per-pair, default
                    #     OFF. If no correct-side level survives, active_levels empties and the
                    #     proximity gate below fails, so the wrong-side fade is refused.
                    if self.config.get("direction_aware_sr", False):
                        if direction == "SELL":
                            active_levels = [l for l in active_levels if l.price >= event.price]
                        else:  # BUY
                            active_levels = [l for l in active_levels if l.price <= event.price]

                    closest_dist = float("inf")
                    closest_lvl = None
                    pip_mult = self.live_evidence._pip_mult
                    for lvl in active_levels:
                        dist_pips = abs(event.price - lvl.price) / pip_mult
                        if dist_pips < closest_dist:
                            closest_dist = dist_pips
                            closest_lvl = lvl
                    
                    if closest_lvl is not None:
                        # Persist the level this entry is actually fading onto the
                        # event, so it reaches signals.jsonl via _log_signal. Without
                        # it, post-hoc "which level lost?" analysis has to re-derive
                        # the level from bars and gets it wrong.
                        event.details["sr_level_type"] = closest_lvl.level_type
                        event.details["sr_level_price"] = float(closest_lvl.price)
                        event.details["sr_level_dist_pips"] = round(closest_dist, 2)
                        event.details["daily_trend"] = getattr(
                            self.live_evidence, "trend_direction_h4", "sideways")
                        # LIVE MTF structural stamp (user 2026-08-21): tag every entry
                        # with its 5Y..5M trend + premium/discount position, for analysis.
                        if self.config.get("mtf_stamp_enabled", True):
                            event.details["mtf_position"] = self._compute_mtf_stamp()

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

                            # 4. MTF structural LOCATION gate — block a BUY at a multi-TF
                            #    PREMIUM (top of the 5Y..5M range) the 20xM15 gate can't see.
                            if is_gate_passed:
                                _ml_ok, _ml_reason = self._mtf_location_gate(
                                    direction, event.details.get("mtf_position"))
                                if not _ml_ok:
                                    is_gate_passed = False
                                    gate_reason = _ml_reason

                            if is_gate_passed:
                                logger.info("LIVE PEAK GATE: S/R Zone Proximity + Trend Aligned! Price=%.5f is %.2f pips from %s level %.5f. Trend=%s, Trade=%s",
                                            event.price, closest_dist, closest_lvl.level_type, closest_lvl.price, daily_trend, direction)
                                # Wide-TP MTF-regime shadow: arm a READ-ONLY virtual wide
                                # bracket (fade + with-trend) on this gated signal.
                                if self.config.get("wide_tp_mtf_shadow_enabled", True):
                                    self._arm_wtms_setup(direction, float(event.price), event.details)

            dashboard = get_dashboard()
            if not self.config.get("test_mode", False) and not (is_peak and is_exhaustion and is_gate_passed):
                self._events_skipped += 1
                # Persist gated fade candidates (not the non-peak noise) so filter
                # effectiveness — esp. direction-aware wrong-side skips — is measurable.
                if is_peak and is_exhaustion:
                    self._log_skip(event, gate_reason or "gate rejected")
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
                self._log_skip(event, f"news guard: {news_reason}")
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

            # DIRECTION FLIP (user-directed 2026-08-21, config flip_direction_enabled).
            # Inverts the fade decision AT SOURCE so every downstream stage (entry gates,
            # sizing, S/R selection, logging, mirror) operates consistently on the flipped
            # direction. Default OFF; set True + restart to arm, set False + restart to revert.
            # ON RECORD: the 3yr backtest shows flipping LOSES (~-1.6p/trade overall,
            # ~-2.9p/trade in the current regime). Kept as a trivially-reversible toggle.
            if self.config.get("flip_direction_enabled", False):
                _orig_signal = signal
                signal = "Sell" if signal == "Buy" else "Buy"
                logger.info("DIRECTION FLIP: %s -> %s (flip_direction_enabled)", _orig_signal, signal)

            # Per-pair entry kill-switch (config entries_enabled; USDJPY OFF 2026-08-18).
            # When False this pair opens NO new positions — every fade is skipped here, at
            # the top of the entry path — but the instance keeps running so any already-open
            # position is still trailed / exited / managed normally. This is the config-level
            # "zero the pair's size". Lead-side gate = the sole origin of new positions, so a
            # skipped entry is never sent to the node either. Bounded: it can only skip.
            if not self.config.get("entries_enabled", True):
                self._events_skipped += 1
                _ee = f"entries disabled for {self.mt5_symbol} (entries_enabled=False)"
                logger.info("SKIPPED (%s)", _ee)
                self._log_skip(event, _ee)
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
                        "reason": _ee,
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Entry filter (default OFF): veto a BUY whose M15 trigger candle
            # closed below its open — the "falling-knife" long. Validated net
            # -2.0 pips/trade, robust out-of-sample and on both symbols. Only
            # applies to M15 triggers (the timeframe the edge was measured on);
            # if the trigger candle is absent or not M15, the trade is allowed.
            knife_tc = event.details.get("trigger_candle")
            if self.config.get("entry_skip_falling_knife", False) and \
                    self._is_falling_knife_buy(signal, knife_tc):
                self._events_skipped += 1
                logger.info("SKIPPED (falling-knife filter: BUY into bearish M15 trigger "
                            "candle O=%.5f C=%.5f)", knife_tc.get("open"), knife_tc.get("close"))
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
                        "reason": "falling-knife filter (BUY into bearish M15 candle)",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Directional BUY-side skips (default OFF; OOS-validated 2026-06/07).
            # BUYs were net-negative in both months; panic-regime and active-session
            # (08-16 UTC) BUYs are the worst pockets. Lead-side only — the node
            # never sees a skipped entry because it is never mirrored.
            buy_skip = self._buy_skip_reason(
                signal, ws.dominant_regime, datetime.now(timezone.utc), self.config)
            if buy_skip:
                self._events_skipped += 1
                logger.info("SKIPPED (BUY-skip filter: %s)", buy_skip)
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
                        "reason": f"BUY-skip filter ({buy_skip})",
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # ── Impulse + breakout + structure shadows (log always; breakout can VETO) ──
            impulse_shadow = self._compute_impulse_shadow(signal)
            breakout_shadow = self._compute_breakout_shadow(signal, event.price)
            structure_shadow = self._compute_structure_shadow(signal, event.price)
            selectivity_shadow = self._compute_selectivity_shadow(signal, event.price, impulse_shadow, event)
            regime_shadow = self._compute_regime_shadow(signal, event.price)

            # Breakout veto (per-pair via SYMBOL_CALIBRATION; USDJPY-armed 2026-08-11).
            # Blocks a fade of a level that is BREAKING (price beyond prior M15
            # structure WITH a sustained push) — the pattern behind both −$190
            # USDJPY losers on 2026-08-10. In-structure reversals (verdict=allow)
            # pass untouched. Lead-side only; a vetoed entry is never mirrored.
            if self.config.get("breakout_veto_enabled", False) and \
                    breakout_shadow.get("verdict") == "would_skip":
                self._events_skipped += 1
                br = (f"breakout veto: {signal} into fresh extreme "
                      f"(ext={breakout_shadow.get('ext_pips')}p push={breakout_shadow.get('push_pips')}p)")
                logger.info("SKIPPED (%s)", br)
                self._log_skip(event, br)
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
                        "reason": br,
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Structure veto (per-pair via SYMBOL_CALIBRATION; both pairs armed 2026-08-12).
            # Skips a fade that FIGHTS the higher-TF trend (against_structure): selling a
            # higher-high in an up-structure / buying a lower-low in a down-structure — the
            # wrong-direction pattern. Like the breakout veto it can only SKIP, never add a
            # loss. with_structure / range pass untouched. Lead-side only; never mirrored.
            _sv_block = False
            if self.config.get("structure_veto_enabled", False) and \
                    structure_shadow.get("verdict") == "against_structure":
                if self.config.get("structure_veto_require_h1_trend", True):
                    # Only veto when a REAL H1 trend opposes the fade. When H1 is
                    # sideways the verdict came from the noisier M15-zigzag fallback,
                    # which thrashes in chop and cuts winning mean-reversion fades —
                    # so leave those alone (see structure_veto_require_h1_trend doc).
                    _h1 = structure_shadow.get("h1_trend")
                    _sv_block = (_h1 in ("up", "down") and _h1 != structure_shadow.get("fade_dir"))
                else:
                    _sv_block = True
            if _sv_block:
                self._events_skipped += 1
                sv = (f"structure veto: {signal} against {structure_shadow.get('m15_structure')} "
                      f"structure (swing={structure_shadow.get('faded_swing')}, "
                      f"h1={structure_shadow.get('h1_trend')})")
                logger.info("SKIPPED (%s)", sv)
                self._log_skip(event, sv)
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
                        "reason": sv,
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Retest priority — engine yields (user-directed 2026-08-14). When the structure-
            # retest is ARMED on this pair and holds a pending setup (a confirmed break-of-
            # structure awaiting its retest) whose direction OPPOSES this fade, the engine defers:
            # the retest's with-trend entry takes precedence over fading against it. Never closes
            # a position — only skips THIS engine entry (lead-only; a skipped entry is not
            # mirrored). Same-direction setups pass (not a conflict). Inert unless the retest is
            # actually enabled, so a shadow-only retest never suppresses the engine.
            _rs = self._retest_setup
            if (self.config.get("structure_retest_engine_yield", True)
                    and self.config.get("structure_retest_enabled", False)
                    and _rs is not None and _rs.get("signal") not in (None, signal)):
                self._events_skipped += 1
                ry = (f"retest priority: engine {signal} yields to armed {_rs.get('signal')} "
                      f"retest (level {_rs.get('level'):.5f})")
                logger.info("SKIPPED (%s)", ry)
                self._log_skip(event, ry)
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
                        "reason": ry,
                        "events_detected": self._events_detected,
                        "events_fired": self._events_fired,
                        "events_skipped": self._events_skipped,
                    })
                continue

            # Consolidation gate (user 2026-08-14; EURUSD-armed, real). Skip a fade fired at the
            # WRONG END of a TIGHT consolidation — the 'entered mid-range, entry became the near
            # support/resistance, got chopped' trap (finer window than the 5h range_gate). Only
            # SKIPS (bounded — a skip never loses); fails OPEN. Lead-side; a skip is not mirrored.
            if self.config.get("consol_gate_enabled", False):
                _cg_ok, _cg_reason = self._consolidation_gate(signal, event.price)
                if not _cg_ok:
                    self._events_skipped += 1
                    logger.info("SKIPPED (%s)", _cg_reason)
                    self._log_skip(event, _cg_reason)
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
                            "reason": _cg_reason,
                            "events_detected": self._events_detected,
                            "events_fired": self._events_fired,
                            "events_skipped": self._events_skipped,
                        })
                    continue

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
                    # Atomic check-and-reserve (race-safe): reserves this entry's USD
                    # direction so a concurrently-firing follower on the other pair
                    # cannot open the conflicting side before this one registers.
                    allow, size_scale, corr_reason = self.correlation_engine.reserve_entry(
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
            # We hold an in-flight correlation reservation from reserve_entry above
            # (whenever the engine is active). It must be cleared exactly once: by
            # register_position on a real fill, else by release_pending below.
            _reserved = self.correlation_engine is not None
            # Staged (confirmation-by-degree) entry: open only a PROBE now, then add
            # the rest on confirmation (see _maybe_fire_stage_add). OFF by default →
            # _stage=False → probe_frac 1.0 → identical to a single full entry.
            _stage = bool(self.config.get("stage_entry_enabled", False))
            _probe_frac = float(self.config.get("stage_probe_frac", 0.40)) if _stage else 1.0
            # Serialize with the structure-retest entry (which runs on the tick thread): both
            # entry paths hold _entry_lock across execute + track/register so they cannot race
            # into two positions on the same symbol+magic (review 2026-08-14).
            self._entry_lock.acquire()
            try:
                trade_result = self.trade_executor.execute_signal(
                    self.mt5_symbol, signal, self.live_state, size_scale, stage_frac=_probe_frac)
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
                        self._active_trade_worst_price[ticket] = trade_result.get("price", 0.0)
                        # Faded S/R level for the shadow-cut observer (read-only; unset -> N/A)
                        self._active_trade_sr_level[ticket] = event.details.get("sr_level_price")
                        self._active_trade_entry_time[ticket] = time.time()
                        if self.correlation_engine is not None:
                            self.correlation_engine.register_position(
                                self.mt5_symbol, signal,
                                trade_result.get("volume", 0.0) or 0.0,
                                trade_result.get("price", 0.0) or 0.0, ticket)
                            _reserved = False   # reservation consumed by the registered position
                        # Arm the staged add: watch this probe for +stage_confirm_pips
                        # favorable within the window; the add fires from the mgmt loop.
                        if _stage:
                            self._arm_stage_add(ticket, signal, size_scale,
                                                trade_result.get("price", 0.0) or 0.0)
                        # Inverse USDJPY mirror (user 2026-08-29): on a real EURUSD fill,
                        # fire the opposite-direction USDJPY order at the same spot. Lead-
                        # side only; inert in single-pair mode. Best-effort, never blocks.
                        if self._is_inverse_mirror_lead():
                            self._fire_follower_inverse(signal)
                    # Mirror this entry decision to the execution node (best-effort;
                    # lead side only — a no-op when mirror_client is None). The staged
                    # PROBE carries its fraction so the node opens the same probe size.
                    _mp = {"cmd": "enter", "signal": signal, "size_scale": size_scale,
                           "lead_lot": trade_result.get("volume")}
                    if _stage:
                        _mp.update({"stage": "probe", "stage_frac": _probe_frac})
                    self._mirror_send(_mp)
            except Exception as ex_err:
                logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)
            finally:
                # No fill / no ticket / error → drop the reservation so this symbol
                # isn't blocked by a phantom in-flight entry.
                if _reserved and self.correlation_engine is not None:
                    self.correlation_engine.release_pending(self.mt5_symbol)
                self._entry_lock.release()

            # Unified per-TF regime map (PHASE 1 shadow labeler; gates NOTHING). Computed
            # here — AFTER the entry — so its per-TF bar pulls can never add fill latency.
            regime_map = self._compute_regime_map(signal, event.price)

            # Persistently log signal to file
            self._log_signal(event, ws, signal, trade_result,
                             impulse_shadow=impulse_shadow, breakout_shadow=breakout_shadow,
                             structure_shadow=structure_shadow, selectivity_shadow=selectivity_shadow,
                             regime_shadow=regime_shadow, regime_map=regime_map)

            # Reversal-confirmation PRE-ENTRY shadow: arm the "wait for confirmation"
            # counterfactual for this fired signal (LEAD only; never acts, never
            # mirrored). Fully fail-safe so a shadow error can't disturb trading.
            if not self._exec_node and self.config.get("revconf_shadow", True) and trade_result:
                try:
                    self._arm_revconf_shadow(event, signal)
                except Exception as _rce:
                    logger.debug("revconf arm failed: %s", _rce)

            # Risk-Engine LIVE observer (SHADOW INTEGRATION; default OFF). Records a
            # HYPOTHETICAL sizing decision from research/risk_engine for this
            # just-executed entry — observe-only. It runs AFTER the order is already
            # placed (cannot perturb it), reads state strictly read-only, never sends/
            # modifies/closes an order, never chooses direction, and writes ONLY to
            # research/risk_engine/shadow_out/. Fully fail-safe + config-gated OFF, so
            # with the flag unset (its default) this branch and its import never run.
            if (not self._exec_node and trade_result
                    and self.config.get("shadow_risk_observer_enabled", False)):
                try:
                    from research.risk_engine.live_observer import observe_entry as _re_observe
                    _re_observe(
                        symbol=self.mt5_symbol, production_signal=signal,
                        live_state=self.live_state, size_scale=size_scale,
                        risk_guard=getattr(self.trade_executor, "risk_guard", None),
                        correlation_engine=self.correlation_engine,
                        trade_result=trade_result, config=self.config,
                        signal_id=str(getattr(event, "id", "") or self._events_detected),
                    )
                except Exception as _roe:
                    logger.debug("shadow risk observer failed: %s", _roe)

            # Set cooldown on event detector
            cooldown = self.config.get("realtime_cooldown_seconds", 300)
            self.event_detector.set_cooldown(cooldown)
            
            # Print stats
            self._log_stats()

    def _compute_impulse_shadow(self, signal: str, lookback_sec: float = 300.0) -> dict:
        """Compute displacement_ratio over the last *lookback_sec* of ticks.

        displacement_ratio = |net move| / total_path.  High ratio (~0.6+) means
        price moved decisively in one direction (breakout / impulse) — fading it
        is dangerous.  Low ratio (~<0.3) means price chopped back and forth
        (exhaustion / trap) — a fade is reasonable.

        Returns a dict suitable for embedding as ``impulse_shadow`` in the signal
        log.  Shadow-only: the trade STILL fires regardless of the verdict.
        """
        try:
            ticks = self.tick_engine.tick_buffer_list
            if len(ticks) < 10:
                return {"verdict": "insufficient_data", "n_ticks": len(ticks)}

            now = ticks[-1]["time"]
            window = [t for t in ticks if (now - t["time"]).total_seconds() <= lookback_sec]
            if len(window) < 10:
                return {"verdict": "insufficient_data", "n_ticks": len(window)}

            mids = [t["mid"] for t in window]
            net_move = abs(mids[-1] - mids[0])
            total_path = sum(abs(mids[i] - mids[i - 1]) for i in range(1, len(mids)))
            disp_ratio = round(net_move / total_path, 4) if total_path > 0 else 0.0

            pip = 0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001
            net_pips = round((mids[-1] - mids[0]) / pip, 1)

            threshold = self.config.get("impulse_disp_threshold", 0.55)
            is_impulse = disp_ratio >= threshold

            move_dir = "up" if mids[-1] > mids[0] else "down"
            fade_dir = "Sell" if signal == "Sell" else "Buy"
            fading_into = (fade_dir == "Sell" and move_dir == "up") or \
                          (fade_dir == "Buy" and move_dir == "down")

            would_skip = is_impulse and fading_into
            return {
                "verdict": "would_skip" if would_skip else "allow",
                "displacement_ratio": disp_ratio,
                "net_pips": net_pips,
                "move_dir": move_dir,
                "is_impulse": is_impulse,
                "fading_into_impulse": fading_into,
                "threshold": threshold,
                "n_ticks": len(window),
                "window_sec": lookback_sec,
            }
        except Exception as e:
            return {"verdict": "error", "error": str(e)}

    def _compute_breakout_shadow(self, signal: str, price: float) -> dict:
        """Distinguish a level that will BREAK (fade loses) from one that will
        HOLD (fade wins), using the prior M15 structure — NOT a trend/direction
        flip (which our own data shows removes USDJPY's winning reversals).

        Today's two −30p USDJPY SELLs both faded the day's HIGH while price was
        making a FRESH multi-hour high — a breakout, not a reversal. USDJPY's
        winning fades sit INSIDE prior structure. So the tell is: is price pushed
        BEYOND the prior structure extreme (a fresh high/low), with a sustained
        directional push in that same direction?

        breakout = (fresh extreme: price beyond the prior-structure high/low by
                    >= breakout_margin_atr × ATR)  AND
                   (directional push: net move of the last breakout_window M15
                    closes >= breakout_push_atr × ATR, in the breakout direction)

        Only that combination logs "would_skip". Every in-structure reversal —
        the real edge — returns "allow". Shadow-only: the trade STILL fires.
        """
        try:
            lookback = int(self.config.get("breakout_lookback", 20))
            win = int(self.config.get("breakout_window", 3))
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-lookback:]
            if len(m15) < max(6, win + 3):
                return {"verdict": "insufficient_data", "n_candles": len(m15)}

            prior = m15[:-win]  # structure BEFORE the recent (possible breakout) bars
            prior_high = max(c.high for c in prior)
            prior_low = min(c.low for c in prior)

            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            atr = self.live_state._state.atr_14_h1 if self.live_state._state else None
            atr_pips = (atr / pip) if atr else 15.0  # fallback if ATR not yet warmed
            margin = float(self.config.get("breakout_margin_atr", 0.25)) * atr_pips
            push_thr = float(self.config.get("breakout_push_atr", 0.5)) * atr_pips

            is_sell = signal.lower() == "sell"
            # Extension of price BEYOND the prior structure, in the breakout
            # (fade-opposing) direction: a SELL fades a HIGH, so its breakout is
            # price pushing ABOVE prior_high; a BUY fades a LOW → below prior_low.
            # PUSH = the trend of the whole structure window (close[0]→close[-1]),
            # NOT just the last few bars: a strong fade-day loser can PAUSE at the
            # extreme for a bar or two before continuing (today's 2nd −30p SELL did
            # exactly that — a 3-bar window read it as flat and let it through).
            if is_sell:
                ext_pips = (price - prior_high) / pip
                push_pips = (m15[-1].close - m15[0].close) / pip        # uptrend toward the high
            else:
                ext_pips = (prior_low - price) / pip
                push_pips = (m15[0].close - m15[-1].close) / pip        # downtrend toward the low
            ext_pips = round(ext_pips, 1)
            push_pips = round(push_pips, 1)

            fresh_extreme = ext_pips >= margin
            strong_push = push_pips >= push_thr
            would_skip = fresh_extreme and strong_push

            return {
                "verdict": "would_skip" if would_skip else "allow",
                "ext_pips": ext_pips,             # how far beyond prior structure
                "push_pips": push_pips,           # recent directional push
                "fresh_extreme": fresh_extreme,
                "strong_push": strong_push,
                "prior_high": round(prior_high, 5),
                "prior_low": round(prior_low, 5),
                "margin_pips": round(margin, 1),
                "push_thr_pips": round(push_thr, 1),
                "atr_pips": round(atr_pips, 1),
                "lookback": lookback, "window": win,
            }
        except Exception as e:
            return {"verdict": "error", "error": str(e)}

    def _compute_structure_shadow(self, signal: str, price: float) -> dict:
        """Stage-1 MTF market-structure LABELER (shadow-only, never gates).

        Builds an M15 zigzag from closed candles (fractal pivots, min-amplitude
        filtered so micro-noise is dropped — the rejection-wick/ADR lesson), labels
        the entry-TF structure up/down/range, and combines it with the existing H1/H4
        EMA trend into a single verdict for THIS fade:
          with_structure    = fade profit-dir agrees with the dominant higher-TF dir
                              (a SELL when the trend is DOWN — selling a lower-high)
          against_structure = fade profit-dir opposes it (SELL in an uptrend — the
                              suspected wrong-direction loser)
          range             = no clear structure
        Also tags the faded swing LH/HH (sell) or HL/LL (buy). Pure label: embedded in
        the signal row via _log_signal; the trade fires unchanged. UNITS: swing
        amplitudes and min_swing are PIPS (diff/pip) — no price-vs-pip mixing.
        """
        try:
            lookback = int(self.config.get("structure_lookback_m15", 40))
            k = int(self.config.get("structure_swing_k", 2))
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-lookback:]
            if len(m15) < (2 * k + 3):
                return {"verdict": "insufficient_data", "n_candles": len(m15)}

            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            atr = self.live_state._state.atr_14_h1 if self.live_state._state else None
            atr_pips = (atr / pip) if atr else (15.0 if pip == 0.01 else 8.0)
            min_swing = float(self.config.get("structure_min_swing_atr", 0.5)) * atr_pips  # pips

            highs = [c.high for c in m15]
            lows = [c.low for c in m15]
            n = len(m15)
            # 1. Fractal pivots: strict local extremum over a +/-k window.
            pivots = []  # (idx, 'H'|'L', price)
            for i in range(k, n - k):
                lh = highs[i - k:i]; rh = highs[i + 1:i + k + 1]
                ll = lows[i - k:i]; rl = lows[i + 1:i + k + 1]
                if highs[i] > max(lh) and highs[i] >= max(rh):
                    pivots.append((i, 'H', highs[i]))
                elif lows[i] < min(ll) and lows[i] <= min(rl):
                    pivots.append((i, 'L', lows[i]))
            # 2. Alternating zigzag with a min-amplitude filter.
            zz = []
            for p in pivots:
                if not zz:
                    zz.append(p); continue
                if p[1] == zz[-1][1]:            # same type — keep the more extreme
                    if (p[1] == 'H' and p[2] > zz[-1][2]) or (p[1] == 'L' and p[2] < zz[-1][2]):
                        zz[-1] = p
                elif abs(p[2] - zz[-1][2]) / pip >= min_swing:   # opposite — accept if big enough
                    zz.append(p)
            sh = [p for p in zz if p[1] == 'H']
            sl = [p for p in zz if p[1] == 'L']

            # 3. Classify M15 structure from the last two swing highs + lows.
            struct = "range"
            if len(sh) >= 2 and len(sl) >= 2:
                hh = sh[-1][2] > sh[-2][2]; hl = sl[-1][2] > sl[-2][2]
                lh_ = sh[-1][2] < sh[-2][2]; ll_ = sl[-1][2] < sl[-2][2]
                if hh and hl:
                    struct = "up"
                elif lh_ and ll_:
                    struct = "down"

            # 4. Higher-TF trend (existing EMA20/50 read) + faded-swing tag.
            h1 = getattr(self.live_evidence, "trend_direction_h1", "sideways")
            h4 = getattr(self.live_evidence, "trend_direction_h4", "sideways")
            fade_dir = "down" if signal == "Sell" else "up"   # fade profit direction
            faded = None
            if signal == "Sell" and len(sh) >= 2:
                faded = "LH" if sh[-1][2] < sh[-2][2] else "HH"
            elif signal == "Buy" and len(sl) >= 2:
                faded = "HL" if sl[-1][2] > sl[-2][2] else "LL"

            # 5. Combined verdict: H1 trend is primary; fall back to M15 zigzag.
            dominant = h1 if h1 in ("up", "down") else struct
            if dominant in ("up", "down"):
                verdict = "with_structure" if dominant == fade_dir else "against_structure"
            else:
                verdict = "range"

            return {
                "verdict": verdict,
                "m15_structure": struct,
                "h1_trend": h1,
                "h4_trend": h4,
                "faded_swing": faded,            # LH/HH (sell) or HL/LL (buy)
                "fade_dir": fade_dir,
                "dominant": dominant,
                "n_swings": len(zz),
                "min_swing_pips": round(min_swing, 1),
                "last_sh": round(sh[-1][2], 5) if sh else None,
                "last_sl": round(sl[-1][2], 5) if sl else None,
            }
        except Exception as e:
            return {"verdict": "error", "error": str(e)}

    # Named S/R level types by which SIDE they sit on. A support (a LOW / floor) is
    # where price bounces UP → SELLING into it is wrong-side. A resistance (a HIGH /
    # ceiling) is where price bounces DOWN → BUYING into it is wrong-side.
    _SUPPORT_TYPES = {"PDL", "NYL", "ASL", "TODAY_L", "LNDL"}
    _RESISTANCE_TYPES = {"PDH", "NYH", "ASH", "TODAY_H", "LNDH"}

    def _compute_selectivity_shadow(self, signal: str, price: float, impulse_shadow: dict, event=None) -> dict:
        """Entry-selectivity LABELER (shadow-only, never gates). Flags marginal / wrong fades
        to cut over-trading + the wrong-direction losses. Two flags fold into would_skip:
        (1) FADING INTO MOMENTUM — validated (n=32: fading_into_impulse=True −$24.9/trade vs
            −$0.8); would_skip when the fade fights the recent 300s move at >= threshold.
        (2) WRONG-SIDE S/R — a SELL fading a SUPPORT (PDL/NYL/… below) or a BUY fading a
            RESISTANCE (above): "sell into support / buy into resistance". EURUSD's
            direction-aware S/R already prevents this; USDJPY (gate OFF) does NOT — this is
            the PDL sells seen 2026-08-12. Type-based, with a price-side fallback for the
            ambiguous M15_SWING/ROUND. The room-veto is logged but NOT used (falsified).
        """
        try:
            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            room_pips = None; range_pos = None
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-20:]
            if len(m15) >= 8:
                hi = max(c.high for c in m15); lo = min(c.low for c in m15)
                span = (hi - lo) or pip
                if signal == "Sell":
                    room_pips = round((price - lo) / pip, 1); range_pos = round((price - lo) / span, 2)
                else:
                    room_pips = round((hi - price) / pip, 1); range_pos = round((hi - price) / span, 2)
            fading = bool((impulse_shadow or {}).get("fading_into_impulse", False))
            disp = float((impulse_shadow or {}).get("displacement_ratio", 0.0) or 0.0)
            thr = float(self.config.get("selectivity_disp_threshold", 0.30))
            momentum_skip = fading and disp >= thr

            # Wrong-side S/R flag (sell into support / buy into resistance).
            level_type = event.details.get("sr_level_type") if event is not None else None
            sr_price = event.details.get("sr_level_price") if event is not None else None
            lvl_is_support = None
            if level_type in self._SUPPORT_TYPES:
                lvl_is_support = True
            elif level_type in self._RESISTANCE_TYPES:
                lvl_is_support = False
            elif sr_price is not None:                       # ambiguous type (M15_SWING/ROUND): use price side
                lvl_is_support = sr_price < price
            wrong_side = bool(
                (signal == "Sell" and lvl_is_support is True) or
                (signal == "Buy" and lvl_is_support is False))

            would_skip = momentum_skip or wrong_side
            reason = ("wrong_side_sr" if wrong_side else
                      ("fading_into_momentum" if momentum_skip else ""))
            return {
                "verdict": "would_skip" if would_skip else "allow",
                "reason": reason,
                "wrong_side": wrong_side, "level_type": level_type,
                "fading_into": fading, "displacement_ratio": round(disp, 3), "threshold": thr,
                "room_pips": room_pips, "range_pos": range_pos,   # logged for regime-tracking (room-veto falsified)
            }
        except Exception as e:
            return {"verdict": "error", "error": str(e)}

    def _compute_regime_shadow(self, signal: str, price: float) -> dict:
        """Regime LABELER (shadow-only, never gates). The dynamic-market lever: the SAME
        fade is right in a RANGE (level holds) and wrong in a TREND (level breaks), and
        regime is what separates the early-cut winners (B) from the wrong-direction losers
        (A). Kaufman efficiency ratio over regime_lookback M15 closes: net move / total
        path. >= regime_trend_er = trending, <= regime_range_er = ranging, else transitional.
        would_skip flags a fade fighting a STRONG trend (the A-mode / "right spot, wrong
        direction"). Logged to prove the split across regimes before any wiring.
        """
        try:
            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            lookback = int(self.config.get("regime_lookback", 20))
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-lookback:]
            if len(m15) < 8:
                return {"regime": "insufficient_data", "n": len(m15)}
            cl = [c.close for c in m15]
            net = abs(cl[-1] - cl[0])
            path = sum(abs(cl[i] - cl[i - 1]) for i in range(1, len(cl)))
            er = round(net / path, 3) if path > 0 else 0.0
            trend_dir = "up" if cl[-1] > cl[0] else "down"
            trend_thr = float(self.config.get("regime_trend_er", 0.50))
            range_thr = float(self.config.get("regime_range_er", 0.30))
            regime = "trending" if er >= trend_thr else ("ranging" if er <= range_thr else "transitional")
            fade_dir = "down" if signal == "Sell" else "up"   # fade profit direction
            against_trend = bool(regime == "trending" and trend_dir != fade_dir)
            # ER EXHAUSTION (whole-chart-behavior 2026-08-14): a move that arrives at the entry
            # ALREADY efficient (high ER-20) continues LESS — the only context feature that
            # survived a cross-pair shuffle-null for remaining-move. Direction-agnostic (unlike
            # against_trend), so it flags high-ER entries regardless of trend match. Shadow flag
            # only — validated at a checkpoint before any skip is wired (EURUSD-first).
            er_exh_thr = float(self.config.get("er_exhaustion_thr", 0.40))
            return {
                "regime": regime,
                "efficiency_ratio": er,
                "trend_dir": trend_dir,
                "net_move_pips": round(net / pip, 1),
                "against_trend": against_trend,
                "er_exhaustion": bool(er >= er_exh_thr),
                "verdict": "would_skip" if against_trend else "allow",   # A-mode: skip fade vs strong trend
                "n": len(m15),
            }
        except Exception as e:
            return {"regime": "error", "error": str(e)}

    # ────────────────────────────────────────────────────────────────────────────
    # UNIFIED PER-TF REGIME MAP — PHASE 1 (shadow labeler; gates NOTHING).
    # A nested {tf -> TFState} object that labels every timeframe trend / retracement /
    # consolidation + its range. Consolidates what the existing gates each recompute and
    # adds RETRACEMENT (a two-TF relation the system currently can't represent). Pure
    # observation, LEAD-only, fully fail-safe. See default_config regime_map_* block.
    # ────────────────────────────────────────────────────────────────────────────
    def _compute_regime_map(self, signal: str, price: float) -> dict:
        """Top-level entry: build (cached) the signal-independent per-TF map, then add the
        signal-dependent roll-up (does THIS fade go WITH or COUNTER to the higher-TF bias).
        Returns the `regime_map` dict logged on the fired signal, or None when disabled.
        Never raises — a labeler error must never disturb an entry."""
        if not self.config.get("regime_map_enabled", True):
            return None
        try:
            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            core = self._build_regime_map_core(price, pip)
            if not core:
                return {"error": "no_data"}
            fade_dir = "down" if signal == "Sell" else "up"     # fade profit direction
            htf = core.get("htf_bias")
            alignment = ("with_htf" if htf == fade_dir else "counter_htf") if htf in ("up", "down") else "mixed"
            out = dict(core)
            out["fade_dir"] = fade_dir
            out["alignment"] = alignment
            return out
        except Exception as e:
            return {"error": str(e)}

    def _build_regime_map_core(self, price: float, pip: float):
        """The signal-INDEPENDENT market-state core (cached ~regime_map_cache_sec). Walks the
        TF list highest->lowest so each TF sees the one above it as its PARENT — the linkage
        retracement needs. Returns {'symbol','as_of_epoch','tfs':{...},'htf_bias'} or None."""
        now = time.time()
        c = getattr(self, "_regime_map_cache", None)
        if c and (now - c.get("t", 0.0)) < float(self.config.get("regime_map_cache_sec", 20)):
            return c.get("val")
        tfs = list(self.config.get("regime_map_tfs", ["D1", "H4", "H1", "M15", "M5"]))
        lookback = int(self.config.get("regime_map_lookback", 60))
        range_lb = int(self.config.get("regime_map_range_lookback", 20))
        rev_atr = float(self.config.get("regime_map_reversal_atr", 0.4))
        states = {}
        parent = None                                            # immediate higher TF's state
        for tf_name in tfs:
            st = self._tf_state(tf_name, price, pip, parent, lookback, range_lb, rev_atr)
            states[tf_name] = st
            if st.get("state") not in ("insufficient_data", "error"):
                parent = st                                      # only a real state can parent the next TF
        val = None
        if any(s.get("state") not in ("insufficient_data", "error") for s in states.values()):
            val = {"symbol": self.mt5_symbol, "as_of_epoch": int(now),
                   "tfs": states, "htf_bias": self._htf_bias(states)}
        self._regime_map_cache = {"t": now, "val": val}
        return val

    def _tf_bars(self, tf_name: str, n: int):
        """Last `n` (open,high,low,close) tuples for `tf_name`. M15 reuses the seeded
        live_evidence deque (so phase-2 parity with the M15 gates is exact); other TFs pull
        their own bars. Returns [] on any miss."""
        try:
            if tf_name.upper() == "M15":
                cds = list(getattr(self.live_evidence, "_m15_candles", []))[-n:]
                return [(c.open, c.high, c.low, c.close) for c in cds]
            tf = getattr(mt5, "TIMEFRAME_" + tf_name.upper(), None)
            if tf is None:
                return []
            rates = mt5.copy_rates_from_pos(self.mt5_symbol, tf, 0, n)
            if rates is None or len(rates) == 0:
                return []
            return [(float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in rates]
        except Exception:
            return []

    def _tf_state(self, tf_name: str, price: float, pip: float, parent, lookback: int,
                  range_lb: int, rev_atr: float) -> dict:
        """Classify ONE timeframe into trend_up/down | retracement | consolidation | unresolved,
        with its range box + evidence. Retracement uses `parent` (the higher TF's state)."""
        try:
            bars = self._tf_bars(tf_name, lookback)
            if len(bars) < 15:
                return {"tf": tf_name, "state": "insufficient_data", "n": len(bars)}
            closes = [b[3] for b in bars]
            # per-TF ATR (avg bar range over last 14) — drives the zigzag reversal + width
            rng14 = [bars[i][1] - bars[i][2] for i in range(max(0, len(bars) - 14), len(bars))]
            atr = sum(rng14) / len(rng14) if rng14 else 0.0
            # Kaufman efficiency ratio (net / path) — same primitive as _compute_regime_shadow
            net = abs(closes[-1] - closes[0])
            path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
            er = round(net / path, 3) if path > 0 else 0.0
            ema_dir = self._ema_dir(closes)
            # structure via a running-extreme shift-zigzag (same shape as _compute_structure_shift)
            R = rev_atr * atr if atr > 0 else 0.0
            highs, lows, _leg = self._zz_swings(bars, R) if R > 0 else ([], [], 0)
            structure = self._classify_structure(highs, lows)
            swing_hi = highs[-1] if highs else None
            swing_lo = lows[-1] if lows else None
            # range box over the last range_lb bars
            rb = bars[-range_lb:]
            r_hi = max(b[1] for b in rb); r_lo = min(b[2] for b in rb)
            span = (r_hi - r_lo) or pip
            range_pos = round((price - r_lo) / span, 3)
            width_atr = round(span / atr, 2) if atr > 0 else None
            # parent linkage (retracement is only defined against a TRENDING parent)
            parent_trend = parent_hi = parent_lo = parent_er = None
            if parent:
                ps = parent.get("state")
                parent_trend = "up" if ps == "trend_up" else ("down" if ps == "trend_down" else None)
                parent_hi = parent.get("swing_hi"); parent_lo = parent.get("swing_lo")
                parent_er = parent.get("er")
            # current child leg vs the parent trend
            rk = min(5, len(closes) - 1)
            recent_dir = "up" if closes[-1] > closes[-1 - rk] else "down"
            counter = (parent_trend == "up" and recent_dir == "down") or \
                      (parent_trend == "down" and recent_dir == "up")
            broke_parent = bool(
                (parent_trend == "up" and parent_lo is not None and price < parent_lo) or
                (parent_trend == "down" and parent_hi is not None and price > parent_hi))
            state = self._classify_state(er, structure, ema_dir, width_atr, parent_trend, counter, broke_parent)
            # depth of the parent leg retraced (0=just began … 1=full retrace / near invalidation)
            retr_depth = None
            if state == "retracement" and parent_hi is not None and parent_lo is not None and parent_hi > parent_lo:
                num = (parent_hi - price) if parent_trend == "up" else (price - parent_lo)
                retr_depth = round(max(0.0, min(1.5, num / (parent_hi - parent_lo))), 3)
            return {
                "tf": tf_name, "state": state,
                "structure": structure, "er": er, "ema_dir": ema_dir,
                "range_hi": round(r_hi, 5), "range_lo": round(r_lo, 5),
                "range_pos": range_pos, "range_width_atr": width_atr,
                "swing_hi": round(swing_hi, 5) if swing_hi is not None else None,
                "swing_lo": round(swing_lo, 5) if swing_lo is not None else None,
                "parent_tf": (parent.get("tf") if parent else None),
                "parent_trend": parent_trend, "retr_depth": retr_depth,
                "confidence": self._state_confidence(state, er, parent_er),
                "n": len(bars),
            }
        except Exception as e:
            return {"tf": tf_name, "state": "error", "error": str(e)}

    @staticmethod
    def _ema_dir(closes) -> str:
        """EMA20/50 stack + last close → up/down/flat (same rule as trend_direction_h1)."""
        if len(closes) < 10:
            return "flat"
        def ema(vals, p):
            k = 2.0 / (p + 1); e = vals[0]
            for v in vals[1:]:
                e = v * k + e * (1 - k)
            return e
        ef = ema(closes, 20); es = ema(closes, min(50, len(closes) - 1)); c = closes[-1]
        if c > ef > es:
            return "up"
        if c < ef < es:
            return "down"
        return "flat"

    @staticmethod
    def _zz_swings(bars, R: float):
        """Running-extreme shift-zigzag over (o,h,l,c) bars, reversal R in PRICE. Returns
        (confirmed_swing_highs, confirmed_swing_lows, current_leg_dir) — chronological."""
        highs = []; lows = []; d = 0; ext = bars[0][3]
        for (_o, h, l, _c) in bars:
            if d >= 0:                                           # up-leg (0 = unknown → up)
                if h > ext: ext = h
                if l <= ext - R: highs.append(ext); d = -1; ext = l
            else:                                                # down-leg
                if l < ext: ext = l
                if h >= ext + R: lows.append(ext); d = 1; ext = h
        return highs, lows, d

    @staticmethod
    def _classify_structure(highs, lows) -> str:
        """HH+HL → up, LH+LL → down, else range (needs >=2 of each swing)."""
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                return "up"
            if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                return "down"
        return "range"

    def _classify_state(self, er, structure, ema_dir, width_atr, parent_trend, counter, broke_parent) -> str:
        """The per-TF state machine (see the regime_map_* config block for the rationale)."""
        trend_er = float(self.config.get("regime_map_trend_er", 0.50))
        range_er = float(self.config.get("regime_map_range_er", 0.30))
        consol_max = float(self.config.get("regime_map_consol_max_atr", 1.8))
        if er >= trend_er and structure in ("up", "down") and ema_dir == structure:
            return "trend_" + structure
        if parent_trend in ("up", "down") and counter and not broke_parent:
            return "retracement"
        # Consolidation is DEFINED by no net progress (low ER) + a tight box; we do NOT also
        # require structure=="range" because a tight chop's zigzag tilts up/down on noise and
        # would spuriously fall through to unresolved. (Retracement is tested first, so a
        # low-ER child of a trending parent moving counter is caught above, not here.)
        if er <= range_er and (width_atr is not None and width_atr <= consol_max):
            return "consolidation"
        return "unresolved"

    def _state_confidence(self, state: str, er: float, parent_er) -> float:
        """Cheap 0..1 confidence: trend = ER vs the trend threshold; consolidation = 1-ER;
        retracement = strength of the parent trend being retraced; unresolved = low."""
        if state.startswith("trend"):
            return round(min(er / max(1e-6, float(self.config.get("regime_map_trend_er", 0.5))), 1.0), 2)
        if state == "consolidation":
            return round(max(0.0, min(1.0, 1.0 - er)), 2)
        if state == "retracement":
            return round(float(parent_er or 0.0), 2)
        return 0.3

    @staticmethod
    def _htf_bias(states) -> str:
        """Higher-TF bias from the two highest AVAILABLE TFs (trend state, else EMA dir).
        up/down only when they agree; else mixed."""
        dirs = []
        for tf in ("W1", "D1", "H4", "H1", "M15", "M5"):
            st = states.get(tf)
            if not st or st.get("state") in ("insufficient_data", "error"):
                continue
            s = st.get("state", "")
            if s == "trend_up":
                dirs.append("up")
            elif s == "trend_down":
                dirs.append("down")
            elif st.get("ema_dir") in ("up", "down"):
                dirs.append(st["ema_dir"])
            if len(dirs) >= 2:
                break
        if not dirs:
            return "mixed"
        if all(d == "up" for d in dirs):
            return "up"
        if all(d == "down" for d in dirs):
            return "down"
        return "mixed"

    def _log_skip(self, event, reason):
        """Persist a REJECTED fade candidate to signals.jsonl (type=signal_skipped).

        Only genuine peak-fade candidates a gate turned away are logged (callers
        guard on is_peak/is_exhaustion), so the file stays analysable: it answers
        "which fades did direction-aware / the range gate / news guard refuse, and
        were they right to?" without scraping daemon.log. event.details already
        carries the peak metrics (velocity_divergence, price_per_tick_efficiency,
        peak_confidence) and, when a level was found, the sr_level_* fields set by
        the proximity check.
        """
        try:
            import os, json
            os.makedirs("reports", exist_ok=True)
            payload = {
                "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": "signal_skipped",
                "mt5_symbol": self.mt5_symbol,
                "event_type": event.event_type.value,
                "event_price": event.price,
                "event_details": event.details,
                "skip_reason": reason,
            }
            with open(self._report_path("signals.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception as e:
            logger.error("Failed to append skip to signals.jsonl: %s", e)

    def _log_revp_telemetry(self, candle):
        """Append one M15 exhaustion-telemetry row for the offline REVP-confluence
        study: velocity_divergence (~reversal pressure) and price_per_tick_efficiency
        (~displacement collapse), as both the bar-close value and the intra-bar
        extreme (div_max / eff_min accumulated per tick in _on_tick).

        LEAD only (the exec-node path returns before this runs); per-instance file
        via _report_path so it never collides with the node; fully fail-safe so a
        logging error can never disturb trading. Consumed later to test whether a
        chart pattern completing AT a level WITH high reversal pressure has an edge.
        """
        try:
            import os, json
            pd = self.event_detector.peak_detector
            div_last = float(getattr(pd, "_last_divergence", 0.0) or 0.0)
            eff_last = float(getattr(pd, "_last_efficiency", 1.0) or 1.0)
            div_max = float(getattr(self, "_revp_div_max", div_last))
            eff_min = float(getattr(self, "_revp_eff_min", eff_last))
            ot = candle.open_time
            os.makedirs("reports", exist_ok=True)
            row = {
                "type": "revp_m15",
                "mt5_symbol": self.mt5_symbol,
                "timestamp_utc": ot.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "epoch": int(ot.replace(tzinfo=timezone.utc).timestamp()),
                "open": round(candle.open, 5), "high": round(candle.high, 5),
                "low": round(candle.low, 5), "close": round(candle.close, 5),
                "div_last": round(div_last, 3), "div_max": round(div_max, 3),
                "eff_last": round(eff_last, 4), "eff_min": round(eff_min, 4),
                "peak_confirmed": bool(getattr(pd, "_last_peak_confirmed", False)),
            }
            with open(self._report_path("revp_telemetry.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.debug("revp telemetry log failed: %s", e)
        finally:
            self._revp_div_max = 0.0
            self._revp_eff_min = 1.0

    def _arm_revconf_shadow(self, event, signal: str) -> None:
        """Arm a reversal-confirmation PRE-ENTRY watch for a fired peak signal.

        Anchored at the SIGNAL price (the decision moment). Over revconf_window_sec a
        per-tick machine (_update_revconf_shadow) resolves confirm/invalidate/timeout;
        on CONFIRM it then simulates a fresh hard-distance trade from the LATER
        (confirmed) price to a win/loss/timeout. Pure shadow: the real trade has
        ALREADY fired; this only records what WAITING would have done. LEAD only; see
        the revconf_shadow docstring in default_config for why there is no arm flag.

        UNITS: every threshold below is in PIPS (×atr_pips), compared against pip-valued
        displacements (price-diff / pip). No price-vs-pip mixing — the recurring bug.
        """
        pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
        atr = self.live_state._state.atr_14_h1 if self.live_state._state else None
        atr_pips = (atr / pip) if atr else (15.0 if pip == 0.01 else 8.0)
        hs = self.config.get("realtime_hard_stop_pips")
        sl_tp = float(hs) if hs else round(2.0 * atr_pips, 1)   # forward sim uses the live hard SL=TP
        confirm_pips = float(self.config.get("revconf_confirm_atr", 0.25)) * atr_pips
        invalidate_pips = float(self.config.get("revconf_invalidate_atr", 0.25)) * atr_pips
        self._revconf_watches.append({
            "phase": "confirm",
            "buy": signal == "Buy",
            "anchor": float(event.price),
            "sig_price": float(event.price),
            "sig_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "t0": time.time(),
            "pip": pip,
            "atr_pips": round(atr_pips, 1),
            "confirm_pips": round(confirm_pips, 1),
            "invalidate_pips": round(invalidate_pips, 1),
            "sl_tp_pips": round(sl_tp, 1),
            "window_sec": float(self.config.get("revconf_window_sec", 900.0)),
            "outcome_window_sec": float(self.config.get("revconf_outcome_window_sec", 14400.0)),
            "level_type": event.details.get("sr_level_type"),
            "would_enter": None, "fwd_t0": None, "fwd_mfe": 0.0, "fwd_mae": 0.0,
            "confirm_delay": None,
        })
        if len(self._revconf_watches) > 20:                    # hard cap, prune oldest
            self._revconf_watches = self._revconf_watches[-20:]

    def _update_revconf_shadow(self, bid: float, ask: float) -> None:
        """Advance pending reversal-confirmation watches one tick. LEAD only, never acts.

        confirm phase: fav = profit-direction displacement from the signal anchor.
          fav >= confirm_pips        → CONFIRM  (start the forward would-enter sim)
          fav <= -invalidate_pips    → would_skip_invalidated (a false turn / continuation)
          elapsed >= window_sec      → would_skip_timeout (never pulled away)
        forward phase (from would_enter, hard SL=TP): adverse-first pessimism.
          MAE <= -sl_tp → loss ; MFE >= sl_tp → win ; elapsed cap → mark-to-market timeout.
        """
        if self._exec_node or not self.config.get("revconf_shadow", True) or not self._revconf_watches:
            return
        try:
            mid = (bid + ask) / 2.0
            now = time.time()
            keep = []
            for w in self._revconf_watches:
                pip = w["pip"]; buy = w["buy"]
                if w["phase"] == "confirm":
                    fav = ((mid - w["anchor"]) if buy else (w["anchor"] - mid)) / pip
                    if fav >= w["confirm_pips"]:
                        w["phase"] = "forward"; w["would_enter"] = mid; w["fwd_t0"] = now
                        w["confirm_delay"] = round(now - w["t0"], 1)
                        w["fwd_mfe"] = 0.0; w["fwd_mae"] = 0.0
                        keep.append(w); continue
                    if fav <= -w["invalidate_pips"]:
                        w["verdict"] = "would_skip_invalidated"; w["adverse_pips"] = round(fav, 1)
                        self._finalize_revconf(w, now); continue
                    if now - w["t0"] >= w["window_sec"]:
                        w["verdict"] = "would_skip_timeout"; w["adverse_pips"] = round(fav, 1)
                        self._finalize_revconf(w, now); continue
                    keep.append(w); continue
                # forward phase
                e = w["would_enter"]
                fav2 = ((mid - e) if buy else (e - mid)) / pip
                if fav2 > w["fwd_mfe"]: w["fwd_mfe"] = fav2
                if fav2 < w["fwd_mae"]: w["fwd_mae"] = fav2
                if -w["fwd_mae"] >= w["sl_tp_pips"]:
                    w["verdict"] = "would_enter"; w["forward_outcome"] = "loss"; w["outcome_pips"] = -w["sl_tp_pips"]
                    self._finalize_revconf(w, now); continue
                if w["fwd_mfe"] >= w["sl_tp_pips"]:
                    w["verdict"] = "would_enter"; w["forward_outcome"] = "win"; w["outcome_pips"] = w["sl_tp_pips"]
                    self._finalize_revconf(w, now); continue
                if now - w["fwd_t0"] >= w["outcome_window_sec"]:
                    w["verdict"] = "would_enter"; w["forward_outcome"] = "timeout"; w["outcome_pips"] = round(fav2, 1)
                    self._finalize_revconf(w, now); continue
                keep.append(w)
            self._revconf_watches = keep[-20:] if len(keep) > 20 else keep
        except Exception as e:
            logger.debug("revconf shadow update failed: %s", e)

    def _finalize_revconf(self, w: dict, now: float) -> None:
        """Write one resolved reversal-confirmation row to reversal_confirm_shadow.jsonl."""
        try:
            import json, os
            row = {
                "type": "reversal_confirm", "mt5_symbol": self.mt5_symbol,
                "sig_ts_utc": w["sig_ts"], "sig_price": w["sig_price"],
                "signal": "Buy" if w["buy"] else "Sell", "level_type": w.get("level_type"),
                "verdict": w["verdict"],
                "confirm_pips": w["confirm_pips"], "invalidate_pips": w["invalidate_pips"],
                "window_sec": w["window_sec"], "atr_pips": w["atr_pips"], "sl_tp_pips": w["sl_tp_pips"],
            }
            if w["verdict"] == "would_enter":
                reprice = ((w["would_enter"] - w["anchor"]) if w["buy"]
                           else (w["anchor"] - w["would_enter"])) / w["pip"]
                row.update({
                    "would_enter_price": round(w["would_enter"], 5),
                    "reprice_pips": round(reprice, 1),          # profit-dir move forfeited before entry
                    "confirm_delay_sec": w["confirm_delay"],
                    "forward_outcome": w["forward_outcome"],
                    "outcome_pips": round(w["outcome_pips"], 1),
                    "fwd_mfe_pips": round(w["fwd_mfe"], 1),
                    "fwd_mae_pips": round(w["fwd_mae"], 1),
                    "fwd_hold_sec": round(now - w["fwd_t0"], 1),
                })
            else:
                row["adverse_pips"] = w.get("adverse_pips")
                row["outcome_pips"] = 0.0                        # skipped → no trade taken
            os.makedirs("reports", exist_ok=True)
            with open(self._report_path("reversal_confirm_shadow.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.debug("revconf finalize failed: %s", e)

    def _update_breakout_retest_shadow(self, candle) -> None:
        """Break-and-retest CONTINUATION shadow (Stage-1 + full lifecycle, no trading).

        The MIRROR of the fade: instead of fading a level, trade WITH the level that
        BREAKS, entering on the retest that confirms it held. Runs on each M15 close
        (LEAD only), tracks a small state machine per setup, and writes completed
        outcomes to reports/breakout_retest_shadow.jsonl. NEVER touches the executor.

        Lifecycle per setup:
          BREAK   current M15 closes beyond the prior structure (>= margin_atr×ATR)
                  with a window trend push (>= push_atr×ATR)  →  arm a retest watch
                  at the broken level (old resistance→support / support→resistance).
          RETEST  price pulls back into level ± retest_tol_atr×ATR.
          CONFIRM after the retest, a bar closes back in the break direction beyond
                  confirm_atr×ATR  →  hypothetical entry at that close.
          FAIL    a bar closes back THROUGH the level by invalidate_atr×ATR (fakeout).
          EXPIRE  no retest within retest_timeout_bars (it ran away).
          OUTCOME from the hypothetical entry, forward bars decide WIN (+tp_atr×ATR)
                  / LOSS (−sl_atr×ATR, adverse checked first = pessimistic) / TIMEOUT.
        Judged later vs the SHUFFLE null before any live entry is ever built.
        """
        if not self.config.get("breakout_retest_shadow", True):
            return
        try:
            import os, json
            cfg = self.config
            lookback = int(cfg.get("br_lookback", 20)); win = int(cfg.get("br_window", 3))
            all_m15 = list(getattr(self.live_evidence, "_m15_candles", []))
            prior_bars = [c for c in all_m15 if c.open_time < candle.open_time]
            if len(prior_bars) < max(6, win + 3):
                return
            m15 = (prior_bars[-(lookback - 1):] + [candle])  # exactly ~lookback, current last
            prior = m15[:-win]
            prior_high = max(c.high for c in prior); prior_low = min(c.low for c in prior)

            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            atr = self.live_state._state.atr_14_h1 if self.live_state._state else None
            atr_pips = (atr / pip) if atr else (15.0 if pip == 0.01 else 8.0)
            atr_px = atr_pips * pip
            # PRICE-unit thresholds (compared against price levels below)…
            margin = float(cfg.get("br_margin_atr", 0.25)) * atr_px
            tol = float(cfg.get("br_retest_tol_atr", 0.2)) * atr_px
            confirm = float(cfg.get("br_confirm_atr", 0.15)) * atr_px
            invalid = float(cfg.get("br_invalidate_atr", 0.35)) * atr_px
            # …and PIP-unit thresholds (compared against pip excursions).
            push_thr = float(cfg.get("br_push_atr", 0.5)) * atr_pips
            sl_p = float(cfg.get("br_sl_atr", 1.0)) * atr_pips
            tp_p = float(cfg.get("br_tp_atr", 2.0)) * atr_pips
            timeout = int(cfg.get("br_retest_timeout_bars", 8))
            out_bars = int(cfg.get("br_outcome_bars", 16))

            hi, lo, cl = candle.high, candle.low, candle.close
            ep = int(candle.open_time.replace(tzinfo=timezone.utc).timestamp())

            def emit(s, status, exit_pips=None):
                row = {"type": "breakout_retest", "mt5_symbol": self.mt5_symbol,
                       "status": status, "dir": s["dir"], "level": round(s["level"], 5),
                       "break_epoch": s["break_epoch"], "break_close": round(s["break_close"], 5),
                       "atr_pips": round(s["atr_pips"], 1), "retest_touched": s["retest_touched"],
                       "entry_epoch": s.get("entry_epoch"), "entry_price": s.get("entry_price"),
                       "fwd_mfe": round(s.get("mfe", 0.0), 1), "fwd_mae": round(s.get("mae", 0.0), 1),
                       "outcome_pips": (round(exit_pips, 1) if exit_pips is not None else None),
                       "sl_pips": round(sl_p, 1), "tp_pips": round(tp_p, 1),
                       "logged_epoch": ep}
                try:
                    os.makedirs("reports", exist_ok=True)
                    with open(self._report_path("breakout_retest_shadow.jsonl"), "a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
                except Exception as we:
                    logger.debug("breakout-retest log failed: %s", we)

            setups = self._brk_setups
            keep = []
            for s in setups:
                s["bars"] += 1
                if s["state"] == "confirmed":
                    e = s["entry_price"]
                    if s["dir"] == "long":
                        s["mfe"] = max(s["mfe"], (hi - e) / pip); s["mae"] = max(s["mae"], (e - lo) / pip)
                    else:
                        s["mfe"] = max(s["mfe"], (e - lo) / pip); s["mae"] = max(s["mae"], (hi - e) / pip)
                    if s["mae"] >= sl_p:                       # adverse first = pessimistic
                        emit(s, "loss", -sl_p); continue
                    if s["mfe"] >= tp_p:
                        emit(s, "win", tp_p); continue
                    if s["ebars"] >= out_bars:
                        px = ((cl - e) / pip) if s["dir"] == "long" else ((e - cl) / pip)
                        emit(s, "timeout", px); continue
                    s["ebars"] += 1; keep.append(s); continue
                # state == watch
                L = s["level"]
                if s["dir"] == "long":
                    if cl < L - invalid:
                        emit(s, "fail"); continue
                    if not s["retest_touched"] and lo <= L + tol:
                        s["retest_touched"] = True
                    if s["retest_touched"] and cl > L + confirm:
                        s["state"] = "confirmed"; s["entry_price"] = cl
                        s["entry_epoch"] = ep; s["ebars"] = 0; s["mfe"] = 0.0; s["mae"] = 0.0
                        emit(s, "confirmed"); keep.append(s); continue
                else:  # short
                    if cl > L + invalid:
                        emit(s, "fail"); continue
                    if not s["retest_touched"] and hi >= L - tol:
                        s["retest_touched"] = True
                    if s["retest_touched"] and cl < L - confirm:
                        s["state"] = "confirmed"; s["entry_price"] = cl
                        s["entry_epoch"] = ep; s["ebars"] = 0; s["mfe"] = 0.0; s["mae"] = 0.0
                        emit(s, "confirmed"); keep.append(s); continue
                if s["bars"] > timeout:
                    emit(s, "expire"); continue
                keep.append(s)
            self._brk_setups = keep

            # Detect a NEW breakout on THIS bar (dedupe: no active setup on same side).
            push_pips = (cl - m15[0].close) / pip           # window trend, in pips
            up = cl > prior_high + margin and push_pips >= push_thr
            dn = cl < prior_low - margin and (-push_pips) >= push_thr
            have_long = any(x["dir"] == "long" and x["state"] == "watch" for x in self._brk_setups)
            have_short = any(x["dir"] == "short" and x["state"] == "watch" for x in self._brk_setups)
            if up and not have_long:
                self._brk_setups.append({"state": "watch", "dir": "long", "level": prior_high,
                    "break_epoch": ep, "break_close": cl, "atr_pips": atr_pips,
                    "bars": 0, "retest_touched": False, "mfe": 0.0, "mae": 0.0})
            if dn and not have_short:
                self._brk_setups.append({"state": "watch", "dir": "short", "level": prior_low,
                    "break_epoch": ep, "break_close": cl, "atr_pips": atr_pips,
                    "bars": 0, "retest_touched": False, "mfe": 0.0, "mae": 0.0})
            if len(self._brk_setups) > 12:                     # hard cap, prune oldest
                self._brk_setups = self._brk_setups[-12:]
        except Exception as e:
            logger.debug("breakout-retest shadow failed: %s", e)

    def _arm_wtms_setup(self, direction, entry, details) -> None:
        """Arm a READ-ONLY virtual wide-bracket PAIR (fade + with-trend) on a gated
        fade signal, stamped with the live MTF cross-regime. Never places an order.

        Tracks BOTH the fade direction and its opposite (with-trend) to a wide bracket
        (SL20/TP100) so the forward good-spot map can score any regime rule offline.
        LEAD/entry-enabled pair only (USDJPY runs entries-off, so it never arms here).
        """
        if not self.config.get("wide_tp_mtf_shadow_enabled", True):
            return
        if not self.config.get("entries_enabled", True):
            return
        try:
            pip = 0.01 if ("JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper()) else 0.0001
            sl_p = float(self.config.get("wtms_sl_pips", 20.0))
            tp_p = float(self.config.get("wtms_tp_pips", 100.0))
            fade_long = str(direction).upper().startswith("B")     # BUY fade = long

            def levels(is_long):
                return (entry - sl_p * pip, entry + tp_p * pip) if is_long \
                    else (entry + sl_p * pip, entry - tp_p * pip)

            f_sl, f_tp = levels(fade_long)
            o_sl, o_tp = levels(not fade_long)
            det = details if isinstance(details, dict) else {}
            lvl = det.get("sr_level_price")
            self._wtms_setups.append({
                "sig_epoch": int(datetime.now(timezone.utc).timestamp()),
                "fade_dir": "Buy" if fade_long else "Sell", "entry": float(entry),
                "pip": pip, "bars": 0, "sl_pips": sl_p, "tp_pips": tp_p,
                "sr_level": (float(lvl) if lvl else None), "level_type": det.get("sr_level_type"),
                "mtf": det.get("mtf_position"),
                "fade": {"long": fade_long, "sl": f_sl, "tp": f_tp, "mfe": 0.0, "mae": 0.0, "out": None},
                "opp": {"long": not fade_long, "sl": o_sl, "tp": o_tp, "mfe": 0.0, "mae": 0.0, "out": None},
            })
            if len(self._wtms_setups) > 200:                       # hard cap, prune oldest
                self._wtms_setups = self._wtms_setups[-200:]
        except Exception as e:
            logger.debug("wtms arm failed: %s", e)

    def _update_wtms_shadow(self, candle) -> None:
        """Resolve the virtual wide brackets on each M15 close (READ-ONLY). When both
        the fade and with-trend legs resolve (or max hold), write one row to
        reports/wide_tp_mtf_shadow.jsonl. NEVER touches the executor."""
        if not self.config.get("wide_tp_mtf_shadow_enabled", True):
            return
        try:
            import os, json
            max_bars = int(float(self.config.get("wtms_max_hold_hours", 120.0)) * 60 / 15)
            hi, lo, cl = candle.high, candle.low, candle.close
            keep = []
            for s in self._wtms_setups:
                s["bars"] += 1
                pip = s["pip"]; e = s["entry"]
                for leg in (s["fade"], s["opp"]):
                    if leg["out"] is not None:
                        continue
                    if leg["long"]:
                        leg["mfe"] = max(leg["mfe"], (hi - e) / pip)
                        leg["mae"] = max(leg["mae"], (e - lo) / pip)
                        hit_sl = lo <= leg["sl"]; hit_tp = hi >= leg["tp"]
                    else:
                        leg["mfe"] = max(leg["mfe"], (e - lo) / pip)
                        leg["mae"] = max(leg["mae"], (hi - e) / pip)
                        hit_sl = hi >= leg["sl"]; hit_tp = lo <= leg["tp"]
                    if hit_sl and hit_tp:
                        leg["out"] = -s["sl_pips"]                  # conservative: adverse first
                    elif hit_tp:
                        leg["out"] = s["tp_pips"]
                    elif hit_sl:
                        leg["out"] = -s["sl_pips"]
                done = s["fade"]["out"] is not None and s["opp"]["out"] is not None
                if done or s["bars"] >= max_bars:
                    for leg in (s["fade"], s["opp"]):               # mark unresolved to close
                        if leg["out"] is None:
                            leg["out"] = ((cl - e) if leg["long"] else (e - cl)) / pip
                    mtf = s.get("mtf") if isinstance(s.get("mtf"), dict) else {}
                    row = {
                        "type": "wtms", "mt5_symbol": self.mt5_symbol, "sig_epoch": s["sig_epoch"],
                        "fade_dir": s["fade_dir"], "entry": round(e, 5),
                        "sr_level": (round(s["sr_level"], 5) if s.get("sr_level") else None),
                        "level_type": s.get("level_type"), "bars_held": s["bars"],
                        "sl_pips": s["sl_pips"], "tp_pips": s["tp_pips"],
                        "fade_pips": round(s["fade"]["out"], 1), "fade_mfe": round(s["fade"]["mfe"], 1),
                        "fade_mae": round(s["fade"]["mae"], 1),
                        "withtrend_pips": round(s["opp"]["out"], 1), "withtrend_mfe": round(s["opp"]["mfe"], 1),
                        "withtrend_mae": round(s["opp"]["mae"], 1),
                        "mtf_summary": mtf.get("summary"), "mtf_macro_zone": mtf.get("macro_zone"),
                        "mtf_macro_pos": mtf.get("macro_pos"), "mtf_intraday_pos": mtf.get("intraday_pos"),
                        "mtf_tfs": mtf.get("tfs"),
                    }
                    try:
                        os.makedirs("reports", exist_ok=True)
                        with open(self._report_path("wide_tp_mtf_shadow.jsonl"), "a", encoding="utf-8") as f:
                            f.write(json.dumps(row) + "\n")
                    except Exception as we:
                        logger.debug("wtms log failed: %s", we)
                else:
                    keep.append(s)
            self._wtms_setups = keep
        except Exception as e:
            logger.debug("wtms shadow failed: %s", e)

    def _log_signal(self, event, ws, signal, trade_result, *, impulse_shadow=None, breakout_shadow=None,
                    structure_shadow=None, selectivity_shadow=None, regime_shadow=None, regime_map=None):
        """Persistently log every generated signal to reports/signals.jsonl and reports/signals.log."""
        import json
        import os

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_payload = {
            "timestamp": timestamp_str,
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "system": event.details.get("system", "optimized"),
            "ticker": self.yf_symbol,
            "mt5_symbol": self.mt5_symbol,
            "event_type": event.event_type.value,
            "event_priority": event.priority.name,
            "event_price": event.price,
            "event_details": event.details,
            "sr_level_type": event.details.get("sr_level_type"),
            "sr_level_price": event.details.get("sr_level_price"),
            "sr_level_dist_pips": event.details.get("sr_level_dist_pips"),
            "daily_trend": event.details.get("daily_trend"),
            "dominant_regime": ws.dominant_regime,
            "regime_confidence": ws.regime_confidence,
            "volatility": ws.volatility_regime,
            "spread_pips": ws.spread_pips,
            "decision": signal,
            "trade_result": trade_result
        }
        if impulse_shadow is not None:
            log_payload["impulse_shadow"] = impulse_shadow
        if breakout_shadow is not None:
            log_payload["breakout_shadow"] = breakout_shadow
        if structure_shadow is not None:
            log_payload["structure_shadow"] = structure_shadow
        if selectivity_shadow is not None:
            log_payload["selectivity_shadow"] = selectivity_shadow
        if regime_shadow is not None:
            log_payload["regime_shadow"] = regime_shadow
        if regime_map is not None:
            log_payload["regime_map"] = regime_map

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
            self._daily_loss_usd = 0.0      # reset the daily-loss-cap accumulator

    def _compute_mtf_stamp(self):
        """LIVE MTF structural stamp for an entry: trend + range-position at every scale
        (5Y..5M) plus the premium/discount read, from MT5 D1/H1/M15/M5 rates. ~60s cached,
        best-effort, read-only — never raises into the entry path. Uses the isolated
        research.mtf_structure module for the pure calc."""
        try:
            now = time.time()
            if self._mtf_cache and (now - self._mtf_cache[0]) < 60.0:
                return self._mtf_cache[1]
            from research.mtf_structure.structure import classify_tf, MTFSnapshot
            pip = 0.01 if "JPY" in (self.mt5_symbol or "").upper() else 0.0001

            def rates(tf, count):
                r = mt5.copy_rates_from_pos(self.mt5_symbol, tf, 0, count)
                if r is None or len(r) == 0:
                    return None
                return ([float(x["high"]) for x in r], [float(x["low"]) for x in r],
                        [float(x["close"]) for x in r])

            d = rates(mt5.TIMEFRAME_D1, 1300); h = rates(mt5.TIMEFRAME_H1, 120)
            m15 = rates(mt5.TIMEFRAME_M15, 60); m5 = rates(mt5.TIMEFRAME_M5, 60)
            base = m5 or h or d
            if base is None:
                return None
            cur = base[2][-1]
            plan = [("5Y", d, 1260), ("1Y", d, 252), ("3M", d, 63), ("1M", d, 21),
                    ("1W", d, 5), ("1D", h, 24), ("1H", h, 12), ("15M", m15, 24), ("5M", m5, 24)]
            tfs = []
            for name, src, bars in plan:
                if src is None:
                    continue
                H, L, C = src
                tf = classify_tf(name, H, L, C, cur, pip, bars)
                if tf is not None:
                    tfs.append(tf)
            if not tfs:
                return None
            snap = MTFSnapshot(price=round(cur, 5), tfs=tfs)
            pdr = snap.premium_discount()
            stamp = {
                "summary": snap.summary(),
                "macro_zone": pdr["macro_zone"], "macro_pos": pdr["macro_pos"],
                "intraday_zone": pdr["intraday_zone"], "intraday_pos": pdr["intraday_pos"],
                "tfs": {t.name: [t.trend, t.position_pct] for t in tfs},
            }
            self._mtf_cache = (now, stamp)
            return stamp
        except Exception as e:
            logger.debug("MTF stamp failed: %s", e)
            return None

    def _mtf_location_gate(self, direction: str, mtf_stamp):
        """MTF structural entry gate off the live 5Y..5M stamp. Two INDEPENDENT checks,
        each flag-gated and each with its own shadow/observe sub-mode; entry-only,
        best-effort — never raises into the entry path. Returns (passed, reason).

        (A) LOCATION veto (``mtf_location_veto_enabled``, currently SHADOW): blocks a
            BUY at a structural PREMIUM (top of the multi-TF range) the short 20xM15
            ``_range_extreme_gate`` cannot see. Backtest showed the live block removed
            winners, so it observes on forward data before arming.

        (B) TREND-ALIGNMENT veto (``mtf_align_veto_enabled``, currently DISARMED):
            blocks a COUNTER-TREND fade — a BUY while the align-TF trend is DOWN, or a
            SELL while it is UP (e.g. the 2026-09-01 BUY faded into a 1H/1W downtrend
            and lost). Pre-written but OFF pending the wide-TP MTF shadow's forward
            good-spot validation (~Sept-21): arm only if counter-trend fades are
            clearly negative across >=2 regimes with real n. Arm path: set
            ``mtf_align_veto_enabled`` True (starts in shadow via ``mtf_align_veto_shadow``
            True -> logs would-block), then ``mtf_align_veto_shadow`` False to enforce."""
        try:
            if not mtf_stamp:
                return True, ""          # no structural read -> don't block

            # (A) premium/discount LOCATION veto (BUY side).
            if self.config.get("mtf_location_veto_enabled", False):
                pos = float(mtf_stamp.get("intraday_pos", 50.0))
                zone = mtf_stamp.get("intraday_zone", "equilibrium")
                thr = float(self.config.get("mtf_location_buy_premium_pos", 60.0))
                if direction == "BUY" and pos >= thr:
                    reason = (f"MTF location: BUY at intraday {zone} ({pos:.0f}% of the "
                              f"multi-TF range) - buying the structural top")
                    if self.config.get("mtf_location_veto_shadow", True):
                        logger.info("MTF LOCATION SHADOW %s: would-block %s - allowed (observe): %s",
                                    self.mt5_symbol, direction, reason)
                    else:
                        return False, reason

            # (B) TREND-ALIGNMENT veto (DISARMED by default): block a counter-trend fade.
            if self.config.get("mtf_align_veto_enabled", False):
                tf = str(self.config.get("mtf_align_veto_tf", "1H"))
                tfs = mtf_stamp.get("tfs", {}) or {}
                v = tfs.get(tf)
                state = str(v[0]).upper() if isinstance(v, (list, tuple)) and v else None
                counter = ((direction == "BUY" and state == "DOWN")
                           or (direction == "SELL" and state == "UP"))
                if counter:
                    reason = (f"MTF align: {direction} against {tf} trend {state} "
                              f"(counter-trend fade)")
                    if self.config.get("mtf_align_veto_shadow", True):
                        logger.info("MTF ALIGN SHADOW %s: would-block %s - allowed (observe): %s",
                                    self.mt5_symbol, direction, reason)
                    else:
                        return False, reason

            return True, ""
        except Exception as e:
            logger.debug("mtf location gate failed: %s", e)
            return True, ""

    def _is_inverse_mirror_lead(self) -> bool:
        """True if THIS daemon is the lead pair whose fills trigger the inverse USDJPY
        mirror, and the multi-pair supervisor is present (single-pair mode -> inert)."""
        return bool(self.config.get("inverse_mirror_enabled", False)
                    and self.supervisor is not None
                    and self.config.get("inverse_mirror_lead", "EURUSD") in (self.mt5_symbol or ""))

    def _fire_follower_inverse(self, lead_signal: str) -> None:
        """Lead-side dispatch: find the follower daemon in the supervisor registry and
        fire its opposite-direction mirror. Best-effort; never raises into the lead
        entry path."""
        try:
            follower = self.config.get("inverse_mirror_follower", "USDJPY")
            for d in self.supervisor.daemons.values():
                if d is self:
                    continue
                if follower in (getattr(d, "mt5_symbol", "") or ""):
                    d.fire_inverse_mirror(lead_signal, self.mt5_symbol)
                    return
            logger.info("INVERSE MIRROR: no %s follower daemon found (single-pair launch?)", follower)
        except Exception as e:
            logger.error("INVERSE MIRROR dispatch failed: %s", e, exc_info=True)

    def fire_inverse_mirror(self, lead_signal: str, lead_symbol: str) -> None:
        """Follower-side: open the OPPOSITE-direction order on THIS pair, triggered by a
        lead-pair fill at the same spot (EURUSD SELL -> USDJPY BUY, the negative
        correlation). Real order on this pair's own executor + live_state. Honors this
        pair's flat guard and daily-loss lockout. Best-effort — never raises to the caller."""
        try:
            if not self.config.get("inverse_mirror_enabled", False):
                return
            if self._sl_locked_out:
                logger.info("INVERSE MIRROR %s: skip — daily-loss cap / lockout active", self.mt5_symbol)
                return
            if self._eod_flat_blocked:
                logger.info("INVERSE MIRROR %s: skip — EOD entry cutoff", self.mt5_symbol)
                return
            inv = "Buy" if str(lead_signal).strip().lower().startswith("s") else "Sell"
            self._entry_lock.acquire()
            try:
                if mt5.positions_get(symbol=self.mt5_symbol) or self._tracked_positions:
                    logger.info("INVERSE MIRROR %s: not flat — skip (lead %s %s)",
                                self.mt5_symbol, lead_symbol, lead_signal)
                    return
                tr = self.trade_executor.execute_signal(self.mt5_symbol, inv, self.live_state, 1.0)
                if tr and tr.get("order"):
                    ticket = tr.get("order")
                    self._tracked_positions.add(ticket)
                    self._active_trade_initial_sl[ticket] = tr.get("sl")
                    self._active_trade_system[ticket] = "inverse_mirror"
                    atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                    self._active_trade_atr[ticket] = atr
                    self._active_trade_peak_price[ticket] = tr.get("price", 0.0)
                    self._active_trade_worst_price[ticket] = tr.get("price", 0.0)
                    self._active_trade_entry_time[ticket] = time.time()
                    if self.correlation_engine is not None:
                        self.correlation_engine.register_position(
                            self.mt5_symbol, inv, tr.get("volume", 0.0) or 0.0,
                            tr.get("price", 0.0) or 0.0, ticket)
                    logger.info("INVERSE MIRROR %s: opened %s (inverse of lead %s %s) ticket=%s vol=%s",
                                self.mt5_symbol, inv, lead_symbol, lead_signal, ticket, tr.get("volume"))
                else:
                    logger.warning("INVERSE MIRROR %s: order returned no ticket (result=%s)",
                                   self.mt5_symbol, tr)
            finally:
                self._entry_lock.release()
        except Exception as e:
            logger.error("INVERSE MIRROR %s failed: %s", self.mt5_symbol, e, exc_info=True)

    def _maybe_engage_sl_lockout(self, reason: str, pips: float = 0.0, profit: float = 0.0) -> None:
        """Accrue realized losses toward the daily-loss cap (or the legacy lockout).

        Two modes, keyed on ``daily_loss_cap_usd``:

        * CAP mode (cap>0): every *losing* close (``profit < 0``) — SL/Stop-Out,
          EOD/weekend flat, or manual — accrues to ``self._daily_loss_usd``, and
          new entries halt only once the day's cumulative loss breaches the cap.
          Any profitable close (``profit >= 0``) is ignored, so a trailed stop
          moved into profit — reported by the broker with the same "sl" comment
          as a real stop — never counts against the pair.

        * LEGACY mode (cap<=0): the pair locks out after a single genuine losing
          stop-out. Here the exit *reason* alone is not sufficient (the profitable
          trailed stop wears the same "Stop Loss (SL) Hit" label), so it gates on
          both the SL/Stop-Out label and ``pips < 0``.

        Cleared by ``_check_daily_reset`` when the trading day rolls.
        """
        cap = float(self.config.get("daily_loss_cap_usd", 0) or 0)
        if cap > 0:
            # DAILY-LOSS-CAP mode (user 2026-08-21): keep trading through single
            # losses; halt new entries only when the day's cumulative realized loss
            # breaches daily_loss_cap_usd. Accrue EVERY losing close (profit < 0) —
            # SL/Stop-Out, EOD/weekend flat, or manual — not just SL-labelled ones,
            # so a non-SL exit (e.g. the Friday weekend flatten) can't dodge the cap.
            # A profitable trailed stop has profit > 0 and is correctly ignored.
            if float(profit) < 0:
                self._daily_loss_usd += float(profit)
                if self._daily_loss_usd <= -cap:
                    if not self._sl_locked_out:
                        logger.info("DAILY LOSS CAP hit for %s: day loss %.2f <= -%.0f; halting new "
                                    "entries until the next trading day", self.mt5_symbol,
                                    self._daily_loss_usd, cap)
                    self._sl_locked_out = True
                else:
                    logger.info("Loss %.2f on %s (%s, day total %.2f / cap -%.0f) — still trading",
                                float(profit), self.mt5_symbol, reason,
                                self._daily_loss_usd, cap)
            return
        # LEGACY one-loss lockout (cap<=0): bar the pair after a single genuine
        # *losing* stop-out only. The exit reason alone is not sufficient — a
        # trailed stop moved into profit wears the same "SL" label — so gate on
        # both the SL/Stop-Out label and pips < 0.
        if not (reason in ("Stop Loss (SL) Hit", "Stop Out (SO)") and pips < 0):
            return
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
        #    minutes before the NY 5pm rollover (ny_close + 3h, DST-aware). With
        #    eod_flatten_weekend_only (user 2026-08-29) this fires ONLY before the
        #    Friday weekly close — positions are HELD overnight Mon–Thu and ride to
        #    their own 20p SL/TP. The daily entry cutoff above is unaffected, and the
        #    one-position-per-pair "not flat" guard means no stacking (it just waits
        #    for the held trade to resolve before taking the next).
        _, _, _, ny_close = get_dst_session_hours(now_utc)
        rollover = (ny_close + 3.0) % 24.0
        before_min = float(self.config.get("eod_flatten_before_close_min", 5))
        flat_after = (rollover - before_min / 60.0) % 24.0             # ~20:55 UTC / 02:25 IST

        # Daily mode: flatten window [flat_after, resume) wrapping past midnight UTC.
        # Weekend-only mode: just the Friday-evening pre-close (weekday()==4, after
        # flat_after); the Sat 00:00–00:30 wrap is dropped (market already closed).
        if bool(self.config.get("eod_flatten_weekend_only", False)):
            in_flat_window = (now_utc.weekday() == 4 and utc_hour >= flat_after)
            flat_reason = "EOD Flat (weekend/Friday close)"
        else:
            in_flat_window = (utc_hour >= flat_after) or (utc_hour < resume)
            flat_reason = "EOD Flat (pre-rollover)"

        # Keyed to the trading day so it fires exactly once (and again after a
        # mid-window restart, which re-adopts and then flattens open positions).
        if in_flat_window:
            td = self._trading_day(now_utc)
            if td != self._eod_flat_tradeday:
                closed = self._close_all_positions(flat_reason)
                self._eod_flat_tradeday = td
                logger.info(
                    "AxonDaemon: %s at %.2fh UTC (rollover=%.2fh) — force-closed %d "
                    "position(s); entries stay blocked until %.2fh UTC (06:00 IST)",
                    flat_reason, utc_hour, rollover, closed, resume,
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

        Used by the pre-news flatten, the EOD pre-rollover flatten, the prop-breach
        flatten, and the manual dashboard close-all button. Each position is closed
        through _close_position (FOK→IOC fallback); the old inline path hardcoded
        ORDER_FILLING_IOC and was observed to fail every attempt on the live
        terminals (2026-07-30 pre-news flatten: 181 consecutive "Unknown" rejects).
        """
        if not mt5 or not mt5.terminal_info():
            logger.warning("Close-all: MT5 not connected, cannot close positions.")
            return 0

        positions = mt5.positions_get(symbol=self.mt5_symbol)
        if not positions:
            return 0

        closed_count = 0
        for pos in positions:
            if pos.magic != self.trade_executor_opt.magic:
                continue
            if self._close_position(pos, reason):
                closed_count += 1
            else:
                logger.warning("Close-all: failed to close position %d (%s).", pos.ticket, reason)

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

            # Calculate profit pips (the actual close price comes from the live
            # tick inside _close_position; bid/ask here only gate the profit test)
            if pos.type == mt5.POSITION_TYPE_BUY:
                profit_pips = (bid - pos.price_open) / pip
            else:
                profit_pips = (pos.price_open - ask) / pip

            # ONLY close if in profit!
            if profit_pips > 0:
                logger.info("EOD: Position %d is in profit (+%.1f pips). Force closing...", pos.ticket, profit_pips)
                # Route through _close_position (FOK→IOC fallback). NOTE: this now
                # honors the `reason` argument for the deal comment; the old inline
                # path hardcoded "EOD profit close" and ignored `reason`.
                if self._close_position(pos, reason):
                    closed_count += 1
                else:
                    logger.warning("EOD: Failed to close position %d (%s).", pos.ticket, reason)
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

    def inject_signal(self, signal: str, size_scale: float = 1.0, source: str = "mirror",
                      lead_lot: Optional[float] = None, stage_frac: float = 1.0,
                      allow_stack: bool = False):
        """Execute an entry routed from the lead brain (execution-node mode).

        Bypasses detection + the entry gauntlet — the lead already applied every
        gate — and runs the engine's OWN order management (``execute_signal``),
        which sizes from THIS terminal's equity and resolves THIS broker's
        ticker/pip. Tracks the resulting position so the daemon's native trailing
        / EOD / exit management picks it up. Returns the executor result or None.

        For a staged (confirmation-by-degree) entry the lead sends TWO enters — a
        PROBE (stage_frac ~0.40) then, on confirmation, an ADD (stage_frac ~0.60,
        stage=="add" → allow_stack). The node stays dumb: it just mirrors each
        tranche at the fraction the lead decided; the confirmation timing lives on
        the lead. Defaults (1.0 / False) = a single full entry, unchanged.
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
                self.mt5_symbol, signal, self.live_state, size_scale, lead_lot,
                stage_frac=stage_frac, allow_stack=allow_stack)
            if trade_result:
                ticket = trade_result.get("order")
                if ticket:
                    self._tracked_positions.add(ticket)
                    self._active_trade_initial_sl[ticket] = trade_result.get("sl")
                    self._active_trade_system[ticket] = source
                    atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                    self._active_trade_atr[ticket] = atr
                    self._active_trade_peak_price[ticket] = trade_result.get("price", 0.0)
                    self._active_trade_worst_price[ticket] = trade_result.get("price", 0.0)
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

    def _consolidation_gate(self, signal: str, price: float):
        """Block a fade fired at the WRONG END of a TIGHT consolidation (user 2026-08-14).

        The 'entered mid-consolidation and got chopped' trap: a fade fires inside a compressed
        range, the entry becomes the NEAR-edge support/resistance, and price bounces off it
        (EURUSD SELL 245090872: sold ~16% up a tight 8p range that then held as support). The
        20-candle range_gate uses a ~5h window and misses the IMMEDIATE consolidation. This is a
        FINER window: over the last consol_lookback closed M15 candles, if the range is tight
        (span <= consol_max_atr x avg-M15-bar) AND the fade sits within consol_edge of the near
        edge (SELL low / BUY high), SKIP. Only ever SKIPS (a skip never loses); fails OPEN so a
        bug can never block everything. Returns (passed, reason). Signal is 'Buy'/'Sell'."""
        try:
            lookback = int(self.config.get("consol_lookback", 8))
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-lookback:]
            if len(m15) < max(5, lookback):
                return True, ""                          # thin history -> don't block
            hi = max(c.high for c in m15); lo = min(c.low for c in m15)
            rng = hi - lo
            if rng <= 0:
                return True, ""
            atr = sum((c.high - c.low) for c in m15) / max(1, len(m15))   # avg M15 bar (self-consistent)
            if atr <= 0:
                return True, ""
            if rng > float(self.config.get("consol_max_atr", 1.8)) * atr:
                return True, ""                          # not tight enough -> gate inactive (normal range)
            edge = float(self.config.get("consol_edge", 0.25))
            rel = (price - lo) / rng
            if signal == "Sell" and rel < edge:
                return False, (f"consolidation gate: SELL in bottom {rel:.2f} of a tight "
                               f"{rng / atr:.1f}xATR consolidation [{lo:.5f}-{hi:.5f}]")
            if signal == "Buy" and rel > (1.0 - edge):
                return False, (f"consolidation gate: BUY in top {rel:.2f} of a tight "
                               f"{rng / atr:.1f}xATR consolidation [{lo:.5f}-{hi:.5f}]")
            return True, ""
        except Exception as e:
            logger.debug("consolidation gate failed: %s", e)
            return True, ""                              # fail OPEN — never block on error

    def _update_retest_verdict(self, pos, bid: float, ask: float, pip: float) -> None:
        """Resolve the retest-confirmation verdict for an open fade (shadow / veto).

        DIAGNOSIS (validated 2026-08-08): the engine fires at the first single-tick
        deceleration, so it fades pauses in live trends — ~72% of realised loss comes
        from fades that go straight against (early MFE ~0, run to the hard stop).

        CONFIRM  = favorable displacement reached +retest_x_pips BEFORE adverse reached
                   retest_adverse_cap_pips, within retest_window_min.
        VETO     = adverse hit first (a straight-against fade).
        TIMEOUT  = neither by the window (never pulled away) — treated as straight-against.

        In SHADOW (default) this only records the verdict + delay, which the close log
        writes next to the trade's realised outcome — so the veto can be proven to cut
        losers, not winners, on live data before it is ever allowed to act. When
        retest_confirm_enabled is set (per-pair, EURUSD-only), a VETO/TIMEOUT scratches
        the position early. Fully guarded — the default config leaves execution unchanged.
        """
        if not (self.config.get("retest_confirm_shadow", True) or
                self.config.get("retest_confirm_enabled", False)):
            return
        ticket = pos.ticket
        arm = self._retest_arm.get(ticket)
        if arm is None or arm.get("verdict") is not None:
            return  # not armed, or already resolved this trade
        try:
            x = float(self.config.get("retest_x_pips", 2.0))
            cap = float(self.config.get("retest_adverse_cap_pips", 2.0))
            window_s = float(self.config.get("retest_window_min", 30)) * 60.0
            t0 = self._active_trade_entry_time.get(ticket)
            elapsed = (time.time() - t0) if t0 else 0.0
            mid = (bid + ask) / 2.0
            # signed favorable displacement in pips (SELL favorable = price DOWN)
            disp = ((mid - arm["anchor"]) if arm["buy"] else (arm["anchor"] - mid)) / pip
            if disp >= x:
                verdict = "confirm"
            elif disp <= -cap:
                verdict = "veto"
            elif elapsed >= window_s:
                verdict = "timeout"
            else:
                return  # still resolving
            arm["verdict"] = verdict
            arm["delay"] = round(elapsed, 1)
            logger.info("RETEST %s: %s ticket %d disp=%.1fp after %.0fs (x=%.1f cap=%.1f)",
                        verdict.upper(), self.mt5_symbol, ticket, disp, elapsed, x, cap)
            # ACT only when explicitly enabled (default OFF). Confirm is a no-op.
            if verdict in ("veto", "timeout") and self.config.get("retest_confirm_enabled", False):
                logger.info("RETEST VETO ENABLED — scratching %s ticket %d early (%s).",
                            self.mt5_symbol, ticket, verdict)
                self._close_position(pos, f"Retest veto ({verdict})")
        except Exception as e:
            logger.error("Retest verdict update failed for ticket %s: %s",
                         getattr(pos, "ticket", "?"), e)

    def _arm_stage_add(self, ticket: int, signal: str, size_scale: float, entry_price: float) -> None:
        """Arm a staged-entry PROBE so its ADD fires on confirmation (see
        _maybe_fire_stage_add). Called only when stage_entry_enabled (lead only)."""
        window_s = float(self.config.get("stage_add_window_min", 30)) * 60.0
        self._stage_arm[ticket] = {
            "signal": signal,
            "buy": signal in ("Buy", "Overweight"),
            "size_scale": size_scale,
            "anchor": entry_price,
            "deadline": time.time() + window_s,
            "done": False,
        }
        logger.info(
            "STAGE ARM %s: probe ticket %d %s @ %.5f — add %.0f%% on +%.1fp within %.0fmin",
            self.mt5_symbol, ticket, signal, entry_price,
            float(self.config.get("stage_add_frac", 0.60)) * 100.0,
            float(self.config.get("stage_confirm_pips", 2.0)),
            float(self.config.get("stage_add_window_min", 30)),
        )

    def _maybe_fire_stage_add(self, pos, bid: float, ask: float, pip: float) -> None:
        """Staged (confirmation-by-degree) entry — add the rest once the fade confirms.

        REPLAY-VALIDATED 2026-08-13 (memory confirmation-by-degree-replay): the probe
        opened on the exhaustion tick; once it pulls +stage_confirm_pips favorable within
        the window, add stage_add_frac as a SECOND position. Fades that go straight-against
        never confirm → they ride at only the probe fraction (the left-tail truncation).
        The add gets its OWN hard-distance SL/TP from its (better) entry and is trailed like
        any position. Fully fail-safe: on any error or missed fill the probe just rides alone;
        this never raises into or blocks the trading path.
        """
        ticket = pos.ticket
        arm = self._stage_arm.get(ticket)
        if not arm or arm.get("done"):
            return
        try:
            now = time.time()
            if now >= arm["deadline"]:
                arm["done"] = True
                logger.info("STAGE ADD %s: probe ticket %d expired unconfirmed — probe rides alone.",
                            self.mt5_symbol, ticket)
                return
            confirm = float(self.config.get("stage_confirm_pips", 2.0))
            mid = (bid + ask) / 2.0
            disp = ((mid - arm["anchor"]) if arm["buy"] else (arm["anchor"] - mid)) / pip
            if disp < confirm:
                return  # not yet confirmed
            # Confirmed. Mark done BEFORE executing so a re-entrant tick can never double-add.
            arm["done"] = True
            add_frac = float(self.config.get("stage_add_frac", 0.60))
            add_res = self.trade_executor.execute_signal(
                self.mt5_symbol, arm["signal"], self.live_state, arm["size_scale"],
                stage_frac=add_frac, allow_stack=True)
            if add_res and add_res.get("order"):
                atk = add_res["order"]
                aprice = add_res.get("price", 0.0) or 0.0
                self._tracked_positions.add(atk)
                self._active_trade_initial_sl[atk] = add_res.get("sl")
                self._active_trade_system[atk] = self._active_trade_system.get(ticket, "optimized")
                self._active_trade_atr[atk] = self._active_trade_atr.get(
                    ticket, self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012)
                self._active_trade_peak_price[atk] = aprice
                self._active_trade_worst_price[atk] = aprice
                self._active_trade_entry_time[atk] = now
                # A confirmed continuation — never let the (disarmed) retest veto re-judge
                # the add: mark its arm resolved, exactly like a restart-adopted position.
                self._retest_arm[atk] = {"anchor": aprice, "buy": arm["buy"],
                                         "verdict": "adopted", "delay": None}
                if self.correlation_engine is not None:
                    # Same symbol+direction as the probe → no new USD conflict; just keep
                    # the engine's view current. Best-effort; never blocks the add.
                    try:
                        self.correlation_engine.register_position(
                            self.mt5_symbol, arm["signal"],
                            add_res.get("volume", 0.0) or 0.0, aprice, atk)
                    except Exception:
                        pass
                logger.info(
                    "STAGE ADD %s: CONFIRMED +%.1fp — added %.0f%% tranche ticket %d vol %s @ %.5f",
                    self.mt5_symbol, disp, add_frac * 100.0, atk, add_res.get("volume"), aprice)
                # Mirror the add to the node (best-effort; carries the add fraction so the
                # node opens the matching second tranche via allow_stack).
                self._mirror_send({"cmd": "enter", "signal": arm["signal"],
                                   "size_scale": arm["size_scale"], "stage": "add",
                                   "stage_frac": add_frac, "lead_lot": add_res.get("volume")})
            else:
                logger.info("STAGE ADD %s: probe ticket %d confirmed but the add produced no fill.",
                            self.mt5_symbol, ticket)
        except Exception as e:
            arm["done"] = True
            logger.error("STAGE ADD %s: error for ticket %s: %s",
                         self.mt5_symbol, getattr(pos, "ticket", "?"), e)

    def _update_mfe_exit_shadow(self, pos, bid: float, ask: float, pip: float) -> None:
        """MFE early-exit shadow: record where a 'dead-fade' cutoff WOULD have exited.

        RATIONALE (today's live data, 2026-08-10): the two −30p USDJPY losers both
        faded a level that broke — MAE ran to ~29.5p (near the full stop) while MFE
        never cleared ~5p. The 7 EURUSD winners all kept MAE < 6p. So MFE alone
        does not separate (a loser reached MFE 6.8), but "adverse has grown past X
        while favorable never cleared Y after a grace window" does.

        RULE (fires once, records a snapshot, NEVER closes here):
          hold >= mfe_exit_grace_sec  AND  running MFE < mfe_exit_floor_pips
          AND running MAE >= mfe_exit_mae_trigger_pips
        The snapshot stores the pips P&L at that instant (would_exit_pips); the close
        log later writes saved_pips = would_exit_pips − actual_close_pips (positive =
        the cutoff would have helped, negative = it would have cut a winner). Pure
        observation, so a threshold can be tuned on live data before it is ever armed.
        """
        if not self.config.get("mfe_exit_shadow", True):
            return
        ticket = pos.ticket
        if self._mfe_exit_shadow.get(ticket, {}).get("fired"):
            return  # already snapshotted the first fire this trade
        try:
            entry = pos.price_open
            peak = self._active_trade_peak_price.get(ticket, entry)
            worst = self._active_trade_worst_price.get(ticket, entry)
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            if is_buy:
                mfe = (peak - entry) / pip
                mae = (entry - worst) / pip
                exit_pips = (bid - entry) / pip          # close a BUY at bid
            else:
                mfe = (entry - peak) / pip
                mae = (worst - entry) / pip
                exit_pips = (entry - ask) / pip           # close a SELL at ask
            t0 = self._active_trade_entry_time.get(ticket)
            hold = (time.time() - t0) if t0 else 0.0

            grace = float(self.config.get("mfe_exit_grace_sec", 900.0))
            floor = float(self.config.get("mfe_exit_floor_pips", 5.0))
            mae_trig = float(self.config.get("mfe_exit_mae_trigger_pips", 10.0))

            if hold >= grace and mfe < floor and mae >= mae_trig:
                self._mfe_exit_shadow[ticket] = {
                    "fired": True,
                    "would_exit_pips": round(exit_pips, 1),
                    "mfe_at_fire": round(mfe, 1),
                    "mae_at_fire": round(mae, 1),
                    "hold_at_fire_sec": int(hold),
                    "grace": grace, "floor": floor, "mae_trigger": mae_trig,
                }
                logger.info("MFE-EXIT SHADOW: %s ticket %d WOULD-EXIT @ %.1fp "
                            "(mfe=%.1f mae=%.1f hold=%.0fs)",
                            self.mt5_symbol, ticket, exit_pips, mfe, mae, hold)
        except Exception as e:
            logger.error("MFE-exit shadow update failed for ticket %s: %s",
                         getattr(pos, "ticket", "?"), e)

    def _update_exit_capture_shadow(self, pos, bid: float, ask: float, pip: float) -> None:
        """Alt-trail exit-capture shadow: where a TIGHTER trail WOULD have exited.

        The live trail leashes 0.50×ATR behind the peak; on EURUSD (ATR≈6p) that
        gives back ≈3p from every peak — the trade is captured at ~42% of its MFE.
        This simulates a tighter leash (`exit_shadow_trail_atr_mult`, default 0.35)
        against the SAME tracked peak, recording the pips at the first bar the tight
        stop would trip. Pure observation — never modifies the real SL. The close
        log pairs it with the actual result so "tighter trail vs let-it-run" can be
        judged on live data before the live trail is touched. (The fixed-TP variants
        are cheaper — computed at close from mfe_pips — see the close logger.)
        """
        if not self.config.get("exit_capture_shadow", True):
            return
        ticket = pos.ticket
        st = self._exit_shadow.get(ticket)
        if st is None:
            st = self._exit_shadow[ticket] = {}
        if st.get("hit") and st.get("gb_hit"):
            return  # both variants recorded
        try:
            atr = self._active_trade_atr.get(ticket)
            peak = self._active_trade_peak_price.get(ticket)
            if not atr or peak is None:
                return
            entry = pos.price_open
            arm_mult = float(self.config.get("trail_trigger_atr_mult", 0.80))
            shadow_mult = float(self.config.get("exit_shadow_trail_atr_mult", 0.35))
            gb_atr = float(self.config.get("exit_shadow_giveback_atr", 0.6))
            is_buy = pos.type == mt5.POSITION_TYPE_BUY
            if is_buy:
                peak_profit = peak - entry
                # (1) existing tighter-leash (arm at 0.8xATR, give-back exit_shadow_trail_atr_mult)
                if not st.get("hit") and peak_profit >= arm_mult * atr:
                    shadow_stop = peak - shadow_mult * atr
                    if bid <= shadow_stop:
                        st.update(hit=True, pips=round((shadow_stop - entry) / pip, 1), mult=shadow_mult)
                # (2) GIVE-BACK trail A/B: armed as soon as in profit, exit on gb_atr give-back from peak
                if not st.get("gb_hit") and peak_profit > 0:
                    gb_stop = peak - gb_atr * atr
                    if bid <= gb_stop:
                        st.update(gb_hit=True, gb_pips=round((gb_stop - entry) / pip, 1))
            else:
                peak_profit = entry - peak
                if not st.get("hit") and peak_profit >= arm_mult * atr:
                    shadow_stop = peak + shadow_mult * atr
                    if ask >= shadow_stop:
                        st.update(hit=True, pips=round((entry - shadow_stop) / pip, 1), mult=shadow_mult)
                if not st.get("gb_hit") and peak_profit > 0:
                    gb_stop = peak + gb_atr * atr
                    if ask >= gb_stop:
                        st.update(gb_hit=True, gb_pips=round((entry - gb_stop) / pip, 1))
        except Exception as e:
            logger.error("Exit-capture shadow update failed for ticket %s: %s",
                         getattr(pos, "ticket", "?"), e)

    def _compute_zigzag(self, R: float, pip: float):
        """M5 swing zigzag over the last N bars, cached ~per minute. R = reversal
        threshold in PRICE. Returns {last_high, last_low, dir, ext} (the most recent
        CONFIRMED swing high/low) or None. Self-contained (pulls its own M5 bars, so it
        works identically on the lead and the node); never raises into the trail path."""
        try:
            now = time.time()
            cache = getattr(self, "_zz_cache", None)
            if cache and (now - cache.get("t", 0.0)) < 60.0:
                return cache.get("zz")
            lookback = int(self.config.get("structure_trail_lookback_m5", 80))
            rates = mt5.copy_rates_from_pos(self.mt5_symbol, mt5.TIMEFRAME_M5, 0, lookback)
            if rates is None or len(rates) < 5:
                return cache.get("zz") if cache else None
            zz = {"dir": 0, "ext": float(rates[0]["close"]), "last_high": None, "last_low": None}
            for r in rates:
                h = float(r["high"]); l = float(r["low"])
                if zz["dir"] >= 0:                       # up-leg (0 = unknown, treat as up)
                    if h > zz["ext"]: zz["ext"] = h
                    if l <= zz["ext"] - R:               # fell R from the peak -> swing HIGH
                        zz["last_high"] = zz["ext"]; zz["dir"] = -1; zz["ext"] = l
                else:                                    # down-leg
                    if l < zz["ext"]: zz["ext"] = l
                    if h >= zz["ext"] + R:               # rose R from the trough -> swing LOW
                        zz["last_low"] = zz["ext"]; zz["dir"] = 1; zz["ext"] = h
            old = cache.get("zz") if cache else None
            if not old or old.get("last_high") != zz["last_high"] or old.get("last_low") != zz["last_low"]:
                logger.info("STRUCT %s: swing pivots — last_high=%s last_low=%s (reversal=%.1fp, dir=%s)",
                            self.mt5_symbol, zz["last_high"], zz["last_low"], R / pip, zz["dir"])
            self._zz_cache = {"t": now, "zz": zz}
            return zz
        except Exception as e:
            logger.debug("structure zigzag failed: %s", e)
            return None

    def _structure_stop(self, pos, pip: float, digits: int):
        """Structure-trail stop price for `pos`: last confirmed lower-high + buffer for a
        SELL / last higher-low − buffer for a BUY. None until a pivot forms (the hard SL
        backstops the warm-up). Only ever the RAW structure level — the caller enforces
        the monotonic ratchet + the valid-side (10016) guard, exactly like the ATR trail."""
        try:
            atr = self._active_trade_atr.get(pos.ticket) or (
                self.live_state._state.atr_14_h1 if self.live_state and self.live_state._state else 0.0)
            R = float(self.config.get("structure_trail_reversal_atr", 0.4)) * (atr or (pip * 40))
            if R <= 0:
                return None
            zz = self._compute_zigzag(R, pip)
            if not zz:
                return None
            buf = float(self.config.get("structure_trail_buffer_pips", 1.0)) * pip
            if pos.type == mt5.POSITION_TYPE_SELL:
                lh = zz.get("last_high")
                return round(lh + buf, digits) if lh else None
            ll = zz.get("last_low")
            return round(ll - buf, digits) if ll else None
        except Exception as e:
            logger.debug("structure stop failed for ticket %s: %s", getattr(pos, "ticket", "?"), e)
            return None

    def _compute_structure_shift(self, pip: float):
        """Shift-zigzag on structure_retest_tf: returns {'highs':[..],'lows':[..],'dir':int,'R':px}
        with the last few CONFIRMED swing highs/lows (most-recent last), or None. Cached ~30s.
        Self-contained (pulls its own bars) — the detection substrate for the structure-retest
        entry trigger. Never raises into the tick loop."""
        try:
            now = time.time()
            c = self._retest_shift_cache
            if c and (now - c.get("t", 0.0)) < 30.0:
                return c.get("val")
            tf_name = str(self.config.get("structure_retest_tf", "M15")).upper()
            tf = getattr(mt5, "TIMEFRAME_" + tf_name, mt5.TIMEFRAME_M15)
            lookback = int(self.config.get("structure_retest_lookback", 48))
            rates = mt5.copy_rates_from_pos(self.mt5_symbol, tf, 0, lookback)
            if rates is None or len(rates) < 8:
                return c.get("val") if c else None
            atr = sum(float(x["high"]) - float(x["low"]) for x in rates[-14:]) / 14.0
            R = float(self.config.get("structure_retest_reversal_atr", 0.4)) * atr
            if R <= 0:
                return None
            d = 0; ext = float(rates[0]["close"]); highs = []; lows = []
            for x in rates:
                h = float(x["high"]); l = float(x["low"])
                if d >= 0:                               # up-leg (0 = unknown, treat as up)
                    if h > ext: ext = h
                    if l <= ext - R:                     # fell R from the peak -> swing HIGH
                        highs.append(ext); d = -1; ext = l
                else:                                    # down-leg
                    if l < ext: ext = l
                    if h >= ext + R:                     # rose R from the trough -> swing LOW
                        lows.append(ext); d = 1; ext = h
            val = {"highs": highs[-3:], "lows": lows[-3:], "dir": d, "R": R}
            self._retest_shift_cache = {"t": now, "val": val}
            return val
        except Exception as e:
            logger.debug("structure shift zigzag failed: %s", e)
            return None

    def _arm_retest_fwd(self, signal: str, entry_px: float, pip: float, fired_real: bool):
        """Arm a forward fixed-rail P&L tracker for a structure-retest would-fire. Resolves to
        structure_retest_shadow.jsonl as WIN(+tp) / LOSS(-sl, adverse-first = pessimistic) /
        TIMEOUT(mark-to-market). Measures ENTRY quality on the live 20/30 rails even when the
        armed exit differs — the shadow that validates the trigger before it is armed."""
        # ER-20 of the move INTO this retest (whole-chart-behavior 2026-08-14): high ER predicts
        # LOWER follow-through — the one cross-pair-robust remaining-move feature. Log it with the
        # realized MFE so a checkpoint can test the exhaustion split on real retest rows.
        er = None
        try:
            _lb = int(self.config.get("regime_lookback", 20))
            m15 = list(getattr(self.live_evidence, "_m15_candles", []))[-_lb:]
            if len(m15) >= 8:
                cl = [c.close for c in m15]
                _net = abs(cl[-1] - cl[0]); _path = sum(abs(cl[i] - cl[i - 1]) for i in range(1, len(cl)))
                er = round(_net / _path, 3) if _path > 0 else 0.0
        except Exception:
            er = None
        self._retest_fwd.append({
            "signal": signal, "entry": entry_px, "pip": pip, "t0": time.time(),
            "mfe": 0.0, "mae": 0.0, "real": bool(fired_real),
            "er": er, "er_exhaustion": (er is not None and er >= float(self.config.get("er_exhaustion_thr", 0.40))),
            "sl": float(self.config.get("structure_retest_sl_pips", 20.0)),
            "tp": float(self.config.get("structure_retest_tp_pips", 30.0)),
        })
        if len(self._retest_fwd) > 40:                   # hard cap, prune oldest
            self._retest_fwd = self._retest_fwd[-40:]

    def _update_retest_fwd(self, bid: float, ask: float, pip: float):
        """Advance + resolve structure-retest forward trackers (LEAD-only). Pure observation —
        reads prices, never touches a position; runs in parallel with the real managed exit."""
        if self._exec_node or not self._retest_fwd:
            return
        try:
            import os, json
            fwd_min = float(self.config.get("structure_retest_forward_min", 90))
            now = time.time(); keep = []
            for t in self._retest_fwd:
                e = t["entry"]
                if t["signal"] == "Sell":
                    fav = (e - bid) / pip; adv = (ask - e) / pip
                else:
                    fav = (ask - e) / pip; adv = (e - bid) / pip
                t["mfe"] = max(t["mfe"], fav); t["mae"] = max(t["mae"], adv)
                status = None; outcome = None
                if t["mae"] >= t["sl"]:                   # adverse checked FIRST = pessimistic
                    status = "LOSS"; outcome = -t["sl"]
                elif t["mfe"] >= t["tp"]:
                    status = "WIN"; outcome = t["tp"]
                elif (now - t["t0"]) >= fwd_min * 60.0:
                    status = "TIMEOUT"; outcome = fav     # mark-to-market at expiry
                if status is None:
                    keep.append(t); continue
                row = {"type": "structure_retest", "mt5_symbol": self.mt5_symbol,
                       "signal": t["signal"], "status": status, "real": t["real"],
                       "entry": round(e, 5), "outcome_pips": round(outcome, 1),
                       "mfe": round(t["mfe"], 1), "mae": round(t["mae"], 1),
                       "er": t.get("er"), "er_exhaustion": t.get("er_exhaustion"),
                       "sl_pips": t["sl"], "tp_pips": t["tp"], "logged_epoch": int(now)}
                try:
                    os.makedirs("reports", exist_ok=True)
                    with open(self._report_path("structure_retest_shadow.jsonl"), "a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
                except Exception as we:
                    logger.debug("structure-retest log failed: %s", we)
            self._retest_fwd = keep
        except Exception as e:
            logger.debug("structure-retest fwd update failed: %s", e)

    def _maybe_fire_structure_retest(self, bid: float, ask: float, pip: float):
        """Structure-shift RETEST entry (LEAD-only, config-gated). Detects a confirmed structure
        shift, arms the broken swing level, and on a retest+rejection fires a WITH-trend entry —
        the entry the exhaustion fader lacks. Shadow-logs every would-fire (+forward fixed-rail
        P&L via _arm_retest_fwd). Places a real order ONLY when structure_retest_enabled, and only
        when flat + past every entry gate. Fully fail-safe — never raises into the tick loop."""
        if self._exec_node or not self.config.get("structure_retest_shadow", True):
            return
        try:
            cfg = self.config
            buf = float(cfg.get("structure_retest_buffer_pips", 1.5)) * pip
            rej = float(cfg.get("structure_retest_reject_pips", 1.0)) * pip
            inv = float(cfg.get("structure_retest_invalidate_pips", 1.0)) * pip
            sh = self._compute_structure_shift(pip)
            if not sh:
                return
            highs = sh["highs"]; lows = sh["lows"]
            setup = self._retest_setup

            # ── (Re)arm on a CONFIRMED break of structure ───────────────────────────
            # A real roll-over needs BOTH a lower high AND a lower low (down-shift), not a bare
            # lower-high — a lone lower-high inside an intact (higher-low) uptrend is a pullback,
            # and arming a sell there is a COUNTER-trend entry (review 2026-08-14). Up-shift is the
            # mirror: a higher low AND a higher high.
            want = None  # (signal, level, invalidation)
            if (len(highs) >= 2 and len(lows) >= 2
                    and highs[-1] < highs[-2] and lows[-1] < lows[-2]):
                want = ("Sell", highs[-1], highs[-2])         # lower-high + lower-low = down-shift
            elif (len(highs) >= 2 and len(lows) >= 2
                    and lows[-1] > lows[-2] and highs[-1] > highs[-2]):
                want = ("Buy", lows[-1], lows[-2])            # higher-low + higher-high = up-shift
            if want is not None:
                if (setup is None or setup["signal"] != want[0]
                        or abs(setup["level"] - want[1]) > 0.5 * pip):
                    setup = {"signal": want[0], "level": want[1], "invalidate": want[2],
                             "touched": False, "extreme": None, "armed_ts": time.time()}
                    self._retest_setup = setup
            if setup is None:
                return

            sig = setup["signal"]; level = setup["level"]; invlvl = setup["invalidate"]

            # ── Invalidation: structure un-shifted (broke back beyond the prior swing) ──
            if sig == "Sell" and ask > invlvl + inv:
                self._retest_setup = None; return
            if sig == "Buy" and bid < invlvl - inv:
                self._retest_setup = None; return

            # ── Retest touch, then rejection back away from the level ──────────────────
            # Track the rejection extreme on the SAME quote the fire test uses, so the spread can't
            # erode the reject threshold (review 2026-08-14): SELL tracks peak BID + fires on a bid
            # fall; BUY tracks trough ASK + fires on an ask rise. (Mixing peak-ask with a bid fall
            # let a fire trigger with ~zero real pullback whenever spread ≈ reject_pips.)
            fire = False
            if sig == "Sell":
                if ask >= level - buf:                        # rallied back into the zone
                    setup["touched"] = True
                if setup["touched"]:
                    setup["extreme"] = bid if setup["extreme"] is None else max(setup["extreme"], bid)
                    if bid <= setup["extreme"] - rej:         # bid fell rej from its peak = rejection
                        fire = True
            else:  # Buy
                if bid <= level + buf:
                    setup["touched"] = True
                if setup["touched"]:
                    setup["extreme"] = ask if setup["extreme"] is None else min(setup["extreme"], ask)
                    if ask >= setup["extreme"] + rej:         # ask rose rej from its trough = rejection
                        fire = True
            if not fire:
                return

            # ── Fire (one per setup) ───────────────────────────────────────────────────
            entry_px = bid if sig == "Sell" else ask
            self._retest_setup = None                          # consume the setup (re-arms on next shift)
            now = time.time()
            # Churn cap for the SHADOW: after a fire the setup re-arms, and a shift level that
            # DRIFTS re-fires every few seconds → the log floods with correlated duplicates (live
            # 2026-08-14: USDJPY buy re-fired as the higher-low walked 158.890->158.855). Cap the
            # shadow to one fire per dedup window; DISTINCT signals minutes apart still log. This
            # is separate from — and shorter than — the real-order cooldown below.
            if (now - self._retest_last_log_ts) < float(cfg.get("structure_retest_shadow_dedup_sec", 180)):
                return
            self._retest_last_log_ts = now
            # Entry gates (same as the exhaustion path): session / SL-lockout / EOD / flat.
            ws = self.live_state.snapshot()
            gated = None
            if ws and ws.session not in self._current_active_sessions():
                gated = "session %s" % ws.session
            elif self._sl_locked_out:
                gated = "SL lockout"
            elif self._eod_flat_blocked:
                gated = "EOD cutoff"
            elif mt5.positions_get(symbol=self.mt5_symbol) or self._tracked_positions:
                gated = "not flat"
            armed = bool(cfg.get("structure_retest_enabled", False))
            cd = float(cfg.get("structure_retest_cooldown_min", 20)) * 60.0
            cooldown = (now - self._retest_last_fire_ts) < cd
            # entries_enabled is the authoritative per-pair kill-switch: even if the retest
            # is armed, a pair with entries disabled (USDJPY 2026-08-18) places no real order.
            # Shadow forward-tracking below still runs (gated only suppresses the LIVE order).
            place_real = armed and (gated is None) and (not cooldown) \
                and self.config.get("entries_enabled", True)
            logger.info("RETEST %s: %s retest of %s-shift level %.5f -> entry %.5f%s%s%s",
                        self.mt5_symbol, sig, ("down" if sig == "Sell" else "up"), level, entry_px,
                        ("" if armed else " [SHADOW]"), (" — GATED (%s)" % gated if gated else ""),
                        (" — COOLDOWN" if (armed and gated is None and cooldown) else ""))
            # Forward shadow tracker ALWAYS (measures RAW signal quality on fixed rails). The
            # cooldown + gates suppress only the LIVE order — never the shadow — else the very
            # WIN/LOSS sample used to decide whether to arm would be biased (review 2026-08-14).
            self._arm_retest_fwd(sig, entry_px, pip, place_real)
            if not place_real:
                return
            self._retest_last_fire_ts = time.time()

            # ── Real order: serialize with the exhaustion entry under _entry_lock and RE-CHECK
            #    flat INSIDE the lock, so the tick-thread retest and the event-thread exhaustion
            #    entry can never both open a position on this symbol+magic (reserve → execute →
            #    register → mirror; reservation cleared exactly once). ──
            self._entry_lock.acquire()
            _reserved = False
            try:
                if mt5.positions_get(symbol=self.mt5_symbol) or self._tracked_positions:
                    logger.info("RETEST %s: not flat at fire (raced the exhaustion entry) — skip",
                                self.mt5_symbol)
                    return
                size_scale = 1.0
                if self.correlation_engine is not None:
                    try:
                        allow, size_scale, _cr = self.correlation_engine.reserve_entry(
                            self.mt5_symbol, sig, self.live_state, self.live_evidence)
                        _reserved = True
                        if not allow:
                            self.correlation_engine.release_pending(self.mt5_symbol); _reserved = False
                            logger.info("RETEST %s: correlation gate blocked (%s)", self.mt5_symbol, _cr)
                            return
                    except Exception:
                        size_scale, _reserved = 1.0, False
                tr = self.trade_executor.execute_signal(self.mt5_symbol, sig, self.live_state, size_scale)
                if tr and tr.get("order"):
                    tk = tr["order"]
                    self._tracked_positions.add(tk)
                    self._active_trade_initial_sl[tk] = tr.get("sl")
                    self._active_trade_system[tk] = "structure_retest"
                    atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                    self._active_trade_atr[tk] = atr
                    self._active_trade_peak_price[tk] = tr.get("price", 0.0)
                    self._active_trade_worst_price[tk] = tr.get("price", 0.0)
                    self._active_trade_entry_time[tk] = time.time()
                    if self.correlation_engine is not None:
                        self.correlation_engine.register_position(
                            self.mt5_symbol, sig, tr.get("volume", 0.0) or 0.0,
                            tr.get("price", 0.0) or 0.0, tk)
                        _reserved = False              # reservation consumed by the registered position
                    self._mirror_send({"cmd": "enter", "signal": sig, "size_scale": size_scale,
                                       "lead_lot": tr.get("volume")})
                    logger.info("RETEST %s: FIRED %s ticket %s vol %s", self.mt5_symbol, sig,
                                tk, tr.get("volume"))
                else:
                    logger.info("RETEST %s: no fill (%s — position open or rejected)", self.mt5_symbol, sig)
            finally:
                if _reserved and self.correlation_engine is not None:
                    self.correlation_engine.release_pending(self.mt5_symbol)
                self._entry_lock.release()
        except Exception as e:
            logger.debug("structure-retest trigger failed: %s", e)

    def _manage_trailing_stops(self, bid: float, ask: float):
        """Manage trailing stop modifications on active MT5 positions."""
        if not mt5 or not mt5.terminal_info():
            return
            
        positions = mt5.positions_get(symbol=self.mt5_symbol)
        if not positions:
            return
            
        pip = 0.01 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 0.0001
        digits = 3 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 5

        # Trail multipliers — now read from config (were hardcoded 0.60/0.80/0.35).
        # trail_dist honors an optional override so a wider trail can be soaked
        # without editing the per-pair spec; default keeps the validated 0.35.
        # Applies to BOTH accounts (each manages its own trail).
        # Exit multipliers. hold_for_profit (per-pair; EURUSD 2026-08-18) makes the WHOLE
        # position ride breakeven-OFF (be_mult 0.0, guarded below) + a wider/later trail —
        # no +1p park — and BYPASS the structure trail (_struct_on=False). Off → the default
        # scalp params + structure trail, unchanged. Applies uniformly (one entry per pair).
        if bool(self.config.get("hold_for_profit_enabled", False)):
            be_mult = float(self.config.get("hold_be_atr_mult", 0.0))
            arm_mult = float(self.config.get("hold_trail_trigger_atr_mult", 1.0))
            trail_mult = float(self.config.get("hold_trail_dist_atr_mult", 0.6))
            _struct_on = False
        else:
            be_mult = float(self.config.get("be_atr_mult", 0.60))
            arm_mult = float(self.config.get("trail_trigger_atr_mult", 0.80))
            trail_mult = self._effective_trail_mult(self.config)
            _struct_on = bool(self.config.get("structure_trail_enabled", False))

        # Trail distance. A FIXED pip leash is used ONLY when hard_distance_mode is on
        # AND a dedicated realtime_hard_trail_pips is configured. Otherwise — including
        # hard 20/30 SL/TP with no explicit fixed trail — the trail is the ADAPTIVE
        # trail_mult × ATR leash: it breathes with volatility (the validated exit),
        # laddering the stop one ATR behind the best price up toward the fixed TP.
        hard_trail_pips = self.config.get("realtime_hard_trail_pips")
        use_fixed_trail = bool(self.config.get("hard_distance_mode") and hard_trail_pips)
        hard_trail_dist = (float(hard_trail_pips) * pip) if use_fixed_trail else None

        # Broker minimum stop distance. The trail target is anchored to the best
        # (peak) price, which only ratchets in the profit direction — so after a
        # retrace it can end up on the WRONG SIDE of the current market (a SELL stop
        # below the ask / a BUY stop above the bid). Re-sending such a target every
        # tick is rejected as retcode 10016 "Invalid stops" and spams the alert log
        # while the stop never advances. We gate every modify on the target being a
        # valid distance on the correct side, so a crossed target is skipped, not
        # re-fired. (stops_level is 0 on this broker; the guard still kills the
        # wrong-side case, which is the actual bug.)
        _si = mt5.symbol_info(self.mt5_symbol)
        _point = (getattr(_si, "point", pip) or pip)
        min_stop_dist = (getattr(_si, "trade_stops_level", 0) or 0) * _point
        # Below this we treat two SL prices as "the same" — so an unchanged stop is
        # not re-sent every tick (float noise / sub-point rounding is ignored).
        sl_change_eps = 0.5 * _point

        for pos in positions:
            ticket = pos.ticket
            # If we don't have the initial SL recorded, initialize it from pos.sl
            if ticket not in self._active_trade_initial_sl:
                self._active_trade_initial_sl[ticket] = pos.sl
                self._tracked_positions.add(ticket)
                atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                self._active_trade_atr[ticket] = atr
                self._active_trade_peak_price[ticket] = pos.price_open
                self._active_trade_worst_price[ticket] = pos.price_open
                # This block is reached ONLY for a position we did NOT originate this
                # session (a restart re-adoption) — a fresh entry is already in
                # _active_trade_initial_sl from the execute path. Mark its retest arm
                # already-RESOLVED ("adopted") so the veto never re-judges a position
                # whose retest history we cannot reconstruct. Restart-safety bug fixed
                # 2026-08-13: a fresh arm here re-ran the veto and scratched 4 adopted
                # positions "after 0s" (−$474). Mirrors the no-progress-abort's fresh-
                # window treatment below.
                self._retest_arm[ticket] = {"anchor": pos.price_open,
                                            "buy": pos.type == mt5.POSITION_TYPE_BUY,
                                            "verdict": "adopted", "delay": None}

            # Ensure ATR and peak price are recorded
            if ticket not in self._active_trade_atr:
                atr = self.live_state._state.atr_14_h1 if self.live_state._state else 0.0012
                self._active_trade_atr[ticket] = atr
            if ticket not in self._active_trade_peak_price:
                self._active_trade_peak_price[ticket] = pos.price_open
            if ticket not in self._active_trade_worst_price:
                self._active_trade_worst_price[ticket] = pos.price_open
            if ticket not in self._retest_arm:
                self._retest_arm[ticket] = {"anchor": pos.price_open,
                                            "buy": pos.type == mt5.POSITION_TYPE_BUY,
                                            "verdict": None, "delay": None}
            # No-progress-abort clock. For an adopted position (first sight after a
            # restart) we reset to "now" rather than the true fill time: purely
            # wall-clock, no server-tz mixing, and it never aborts a position the
            # instant we re-adopt it — it just grants a fresh window.
            if ticket not in self._active_trade_entry_time:
                self._active_trade_entry_time[ticket] = time.time()

            # Retest-confirmation verdict (shadow logs it; veto acts only when armed).
            self._update_retest_verdict(pos, bid, ask, pip)
            # Staged-entry ADD: fire the remaining tranche once the probe confirms
            # (lead only; no-op unless stage_entry_enabled armed this probe).
            if not self._exec_node and self._stage_arm:
                self._maybe_fire_stage_add(pos, bid, ask, pip)
            # MFE early-exit shadow (records where a dead-fade cutoff WOULD exit).
            self._update_mfe_exit_shadow(pos, bid, ask, pip)
            # Exit-capture shadow (records where a TIGHTER trail WOULD exit).
            self._update_exit_capture_shadow(pos, bid, ask, pip)
            # Shadow "level broke -> cut" observer (lead-only; logs would-cut, never
            # touches the position). Inert unless shadow_cut_enabled; lazily built.
            if not self._exec_node and self.config.get("shadow_cut_enabled", False):
                try:
                    if self._shadow_cut is None:
                        from research.exit_cut_forensics.shadow_cut import ShadowCutTracker
                        self._shadow_cut = ShadowCutTracker(
                            buffer_pips=float(self.config.get("shadow_cut_buffer_pips", 3.0)),
                            enabled=True)
                    _init_sl = self._active_trade_initial_sl.get(ticket)
                    _sl_pips = (abs(pos.price_open - _init_sl) / pip
                                if _init_sl else None)
                    self._shadow_cut.observe(
                        ticket=ticket,
                        direction=("SELL" if pos.type == mt5.POSITION_TYPE_SELL else "BUY"),
                        entry=pos.price_open,
                        sr_level=self._active_trade_sr_level.get(ticket),
                        symbol=self.mt5_symbol, bid=bid, ask=ask,
                        sl_pips=_sl_pips, epoch=int(time.time()))
                except Exception as _sce:
                    logger.debug("shadow-cut observe failed: %s", _sce)

            atr = self._active_trade_atr[ticket]
            initial_sl = self._active_trade_initial_sl[ticket]

            if initial_sl <= 0.0:
                continue

            # No-progress abort (default OFF): scratch a position that has not
            # reached the minimum favorable excursion within the time window.
            # Runs before the trail logic so a dead trade is cut, not trailed.
            if self.config.get("entry_noprogress_abort", False):
                if self._maybe_noprogress_abort(pos, bid, ask, pip):
                    continue
                
            # Trustworthy current stop for the monotonic ratchet. positions_get()
            # is read UNLOCKED from several concurrent threads (both pair loops +
            # dashboard/mirror + the executor's unfiltered read) against the
            # non-thread-safe MT5 client, while only WRITES take mt5_lock. A torn
            # cross-thread read returns pos.sl == 0.0 (or a stale/garbled value)
            # for a position that genuinely holds a stop — confirmed live
            # 2026-08-14 on Eightcap-Demo (USDJPY.i ticket 245071449: broker held
            # SL 159.006 and closed on it, while pos.sl read 0.0). The old code
            # keyed the ratchet baseline AND the fire test off pos.sl, so a false 0
            # both (a) re-fired an identical modify every tick and (b) let a RISING
            # structure stop LOOSEN the real stop (158.994 -> 159.006, defeating the
            # ratchet). Drive everything off our own shadow of the last SL we
            # actually set instead; we are the sole writer of the SL, so it stays
            # authoritative regardless of how the read races. (freeze/stops level is
            # 0 here, so a genuinely-tight stop is valid and not a freeze artifact.)
            known_sl = self._current_known_sl(pos)
            # A real no-SL is nearly impossible here (initial_sl > 0 is enforced
            # above), but if the shadow is somehow empty, VERIFY with a serialized
            # re-read before the trail establishes one from scratch — never act on a
            # lone unlocked 0.
            genuine_no_sl = known_sl <= 0.0 and self._verify_broker_no_sl(ticket)
            if genuine_no_sl:
                known_sl = 0.0

            if pos.type == mt5.POSITION_TYPE_BUY:
                # Update peak price using the bid price
                self._active_trade_peak_price[ticket] = max(self._active_trade_peak_price[ticket], bid)
                peak_price = self._active_trade_peak_price[ticket]
                # Update worst (adverse) price for MAE — a BUY's adverse move is DOWN
                self._active_trade_worst_price[ticket] = min(
                    self._active_trade_worst_price.get(ticket, pos.price_open), bid)

                # Check Breakeven Trigger (0.60 * ATR)
                profit = bid - pos.price_open
                be_trigger = be_mult * atr

                # Check Trailing Stop (once peak profit >= 0.80 * ATR, trail by 0.35 * ATR)
                peak_profit = peak_price - pos.price_open
                trail_trigger = arm_mult * atr

                target_sl = known_sl
                # be_mult > 0.0 guard: the runner leg sets be_mult 0.0 = breakeven DISABLED.
                # Without the guard, be_trigger would be 0 and this would lock breakeven the
                # instant the trade is profitable — the exact +1p park the runner must avoid.
                if be_mult > 0.0 and profit >= be_trigger:
                    breakeven_sl = round(pos.price_open + 1 * pip, digits)
                    if target_sl < breakeven_sl or genuine_no_sl:
                        target_sl = breakeven_sl

                # Structure-trail (when armed) REPLACES the ATR trail: ratchet the stop up
                # to just under the last confirmed higher-low. Falls back to the ATR trail
                # when OFF or before a pivot forms. Same monotonic-tighten + valid-side guard.
                # _struct_on is False for a runner leg (it uses the wide ATR trail instead).
                if _struct_on:
                    struct_sl = self._structure_stop(pos, pip, digits)
                    # Breakeven floor (see SELL branch): only tighten to a structure stop that
                    # locks breakeven-or-better (at/above entry for a BUY); until then keep the
                    # wider hard stop so a routine pullback inside the noise can't scratch it.
                    _be_floor_ok = (not self.config.get("structure_trail_be_floor", True)) or (
                        struct_sl is not None and struct_sl >= pos.price_open)
                    if struct_sl is not None and _be_floor_ok and (target_sl < struct_sl or genuine_no_sl):
                        target_sl = struct_sl
                elif peak_profit >= trail_trigger:
                    trail_distance = hard_trail_dist if use_fixed_trail else trail_mult * atr
                    trail_sl = round(peak_price - trail_distance, digits)
                    if target_sl < trail_sl or genuine_no_sl:
                        target_sl = trail_sl

                # Fire only when the target is a REAL tighten over the stop the broker
                # actually holds (higher for a BUY), or we verified there is none —
                # never on a false 0-read, and never re-send an unchanged stop.
                if (target_sl > known_sl + sl_change_eps or genuine_no_sl) and target_sl > 0.0:
                    # A BUY stop must sit BELOW the market by >= the broker min
                    # distance. After a retrace the peak-anchored target can be at/
                    # above the bid -> invalid (10016); skip rather than re-fire.
                    if target_sl <= bid - min_stop_dist:
                        logger.info("AxonDaemon: Trailing SL triggered for BUY ticket %d. Modifying SL: %.5f -> %.5f",
                                    ticket, known_sl, target_sl)
                        if self._modify_sl(ticket, target_sl, pos.tp, "BUY"):
                            self._active_trade_sl[ticket] = target_sl
                    else:
                        logger.debug("AxonDaemon: BUY ticket %d trail target %.5f not below market "
                                     "(bid %.5f, min-dist %.5f) — crossed/too-close, SL stays %.5f",
                                     ticket, target_sl, bid, min_stop_dist, known_sl)

            elif pos.type == mt5.POSITION_TYPE_SELL:
                # Update peak price using the ask price
                self._active_trade_peak_price[ticket] = min(self._active_trade_peak_price[ticket], ask)
                peak_price = self._active_trade_peak_price[ticket]
                # Update worst (adverse) price for MAE — a SELL's adverse move is UP
                self._active_trade_worst_price[ticket] = max(
                    self._active_trade_worst_price.get(ticket, pos.price_open), ask)

                # Check Breakeven Trigger (0.60 * ATR)
                profit = pos.price_open - ask
                be_trigger = be_mult * atr

                # Check Trailing Stop (once peak profit >= 0.80 * ATR, trail by 0.35 * ATR)
                peak_profit = pos.price_open - peak_price
                trail_trigger = arm_mult * atr

                target_sl = known_sl
                # be_mult > 0.0 guard: runner leg disables breakeven (be_mult 0.0); without it
                # be_trigger=0 would park the stop at breakeven the moment the trade is green.
                if be_mult > 0.0 and profit >= be_trigger:
                    breakeven_sl = round(pos.price_open - 1 * pip, digits)
                    if target_sl > breakeven_sl or genuine_no_sl:
                        target_sl = breakeven_sl

                # Structure-trail (when armed) REPLACES the ATR trail: ratchet the stop down
                # to just above the last confirmed lower-high. Falls back to the ATR trail
                # when OFF or before a pivot forms. Same monotonic-tighten + valid-side guard.
                # _struct_on is False for a runner leg (it uses the wide ATR trail instead).
                if _struct_on:
                    struct_sl = self._structure_stop(pos, pip, digits)
                    # Breakeven floor: only TIGHTEN to a structure stop that locks breakeven-or-
                    # better (at/below entry for a SELL). Parking it at a small-LOSS level just
                    # above a nearby swing high sits it INSIDE the noise and gets scratched by a
                    # routine counter-spike right before the move runs (USDJPY 245071449
                    # 2026-08-14: struct stop 2.4p from entry, tagged by an 11p spike, price then
                    # fell into profit). Until structure can lock BE+, keep the wider hard stop.
                    _be_floor_ok = (not self.config.get("structure_trail_be_floor", True)) or (
                        struct_sl is not None and struct_sl <= pos.price_open)
                    if struct_sl is not None and _be_floor_ok and (target_sl > struct_sl or genuine_no_sl):
                        target_sl = struct_sl
                elif peak_profit >= trail_trigger:
                    trail_distance = hard_trail_dist if use_fixed_trail else trail_mult * atr
                    trail_sl = round(peak_price + trail_distance, digits)
                    if target_sl > trail_sl or genuine_no_sl:
                        target_sl = trail_sl

                # Fire only when the target is a REAL tighten over the stop the broker
                # actually holds (lower for a SELL), or we verified there is none —
                # never on a false 0-read, and never re-send an unchanged stop. This
                # is what stops the RISING structure stop from loosening the ratchet.
                if (target_sl < known_sl - sl_change_eps or genuine_no_sl) and target_sl > 0.0:
                    # A SELL stop must sit ABOVE the market by >= the broker min
                    # distance. After a retrace up, the peak-anchored target can be
                    # at/below the ask -> invalid (10016); skip rather than re-fire.
                    if target_sl >= ask + min_stop_dist:
                        logger.info("AxonDaemon: Trailing SL triggered for SELL ticket %d. Modifying SL: %.5f -> %.5f",
                                    ticket, known_sl, target_sl)
                        if self._modify_sl(ticket, target_sl, pos.tp, "SELL"):
                            self._active_trade_sl[ticket] = target_sl
                    else:
                        logger.debug("AxonDaemon: SELL ticket %d trail target %.5f not above market "
                                     "(ask %.5f, min-dist %.5f) — crossed/too-close, SL stays %.5f",
                                     ticket, target_sl, ask, min_stop_dist, known_sl)

    @staticmethod
    def _effective_trail_mult(config) -> float:
        """Trailing-stop distance (× ATR): the override if set, else the per-pair
        trail_dist_atr_mult (default 0.35). An explicit 0.0 override is honored."""
        ov = config.get("trail_dist_atr_mult_override")
        return float(ov) if ov is not None else float(config.get("trail_dist_atr_mult", 0.35))

    def _deal_time_local(self, epoch) -> datetime:
        """Convert an MT5 deal/tick epoch to a real local-time instant.

        MT5 stamps deals with the BROKER SERVER wall clock (EET/EEST, UTC+2/+3)
        packed into a Unix-epoch field, so it is not a true epoch. Feeding it
        straight to datetime.fromtimestamp() adds the local offset on top of the
        server offset and over-reports the close time by the broker offset
        (measured at exactly +3h against tick time). Subtract it first.
        """
        try:
            offset_h = get_broker_tz_offset(self.mt5_symbol)
        except Exception:
            offset_h = 0
        return datetime.fromtimestamp(float(epoch) - offset_h * 3600)

    @staticmethod
    def _is_falling_knife_buy(signal: str, trigger_candle) -> bool:
        """True if this is a BUY whose M15 trigger candle closed below its open.

        The validated 'falling-knife' long (net -2.0 pips/trade over 197 trades,
        robust out-of-sample and on both symbols). Only M15 triggers qualify —
        that is the timeframe the edge was measured on; anything else (or a
        missing candle) returns False so the trade is allowed through.
        """
        if signal != "Buy":
            return False
        tc = trigger_candle or {}
        o, c = tc.get("open"), tc.get("close")
        return tc.get("timeframe") == "M15" and o is not None and c is not None and c < o

    @staticmethod
    def _buy_skip_reason(signal: str, dominant_regime, now_utc, config) -> Optional[str]:
        """Reason string if this BUY should be vetoed by a config-gated directional
        filter, else None. All three gates default OFF.

        Validated OOS on 2026-06 (n=57) and 2026-07 (n=144); a pocket only ships if
        it is net-negative in BOTH months independently. BUYs were net-negative in
        both (-40 / -27) while SELLs carried the edge (+103 / +340):

          * panic-regime BUY   -58 (n=8)  / -30 (n=14)
          * active-session BUY -68 (n=18) / -74 (n=44)   [08-16 UTC]

        The session window is 08-16 UTC and is bucketed on true UTC. An earlier
        07-12 window was derived from local timestamps mistaken for UTC+3 and was
        wrong by 3 hours — it skipped hour 07, which is net-POSITIVE in both months
        (+28 / +29), and missed the negative 12-16 block. Neighbouring windows
        (08-17, 09-16, 10-16) agree, so the region is stable rather than a
        knife-edge fit. Caveat: both months shared a down-trend, so this is partly
        a directional bet, not a pure time-of-day effect.
        """
        if signal != "Buy":
            return None
        if config.get("entry_skip_all_buy", False):
            return "all-BUY suppression"
        if config.get("entry_skip_panic_buy", False) and str(dominant_regime or "").lower() == "panic":
            return "panic-regime BUY"
        if config.get("entry_skip_session_buy", False) and now_utc is not None:
            start = int(config.get("entry_skip_session_buy_start", 8))
            end = int(config.get("entry_skip_session_buy_end", 16))
            if start <= now_utc.hour < end:
                return f"active-session BUY ({start:02d}-{end:02d} UTC)"
        return None

    def _maybe_noprogress_abort(self, pos, bid: float, ask: float, pip: float) -> bool:
        """Scratch a position that hasn't made progress within the time window.

        Fires only when the peak favorable excursion is still below
        ``noprogress_abort_min_favorable_pips`` after ``noprogress_abort_minutes``
        from entry — the "wrong-from-entry" signature (MFE ~ 0). Returns True if
        the position was closed (caller should skip trailing it this tick).
        """
        ticket = pos.ticket
        entry_t = self._active_trade_entry_time.get(ticket)
        if entry_t is None:
            return False
        age_min = (time.time() - entry_t) / 60.0
        if age_min < float(self.config.get("noprogress_abort_minutes", 12.0)):
            return False

        min_fav = float(self.config.get("noprogress_abort_min_favorable_pips", 2.0))
        peak = self._active_trade_peak_price.get(ticket, pos.price_open)
        if pos.type == mt5.POSITION_TYPE_BUY:
            fav_pips = max((bid - pos.price_open) / pip, (peak - pos.price_open) / pip)
            side = "BUY"
        else:
            fav_pips = max((pos.price_open - ask) / pip, (pos.price_open - peak) / pip)
            side = "SELL"
        if fav_pips >= min_fav:
            return False  # made enough progress — let the trail/breakeven logic run

        # Notice-only soak: log the decision but leave the position open, so the
        # abort can be observed on the live account before it is armed.
        if self.config.get("noprogress_abort_notice_only", True):
            logger.info("AxonDaemon: No-progress abort [NOTICE-ONLY] — would scratch %s ticket %d "
                        "aged %.1fmin, peak favorable %.1f pips (< %.1f). Left open.",
                        side, ticket, age_min, fav_pips, min_fav)
            return False

        logger.info("AxonDaemon: No-progress abort — %s ticket %d aged %.1fmin, peak favorable "
                    "%.1f pips (< %.1f). Scratching at market.", side, ticket, age_min, fav_pips, min_fav)
        ok = self._close_position(pos, "No-progress abort")
        if ok:
            try:
                send_alert(f"No-progress abort: {self.mt5_symbol} {side} ticket {ticket} scratched "
                           f"after {age_min:.0f}min (peak favorable {fav_pips:.1f} pips).", self.config)
            except Exception as e:
                logger.error("No-progress abort alert failed: %s", e)
        return ok

    def _close_position(self, pos, reason: str) -> bool:
        """Market-close a single position, FOK then IOC fallback. Returns True on fill.

        Mirrors the trade-executor's filling-mode fallback (FOK is what works on
        the Eightcap terminal; the older ``_close_all_positions`` path hardcodes
        IOC and was observed to fail every attempt). The resulting close is picked
        up by ``_check_for_closed_positions`` on the next tick, which logs the exit
        and mirrors it to the exec-node — so no explicit mirror send is needed here.
        """
        if not mt5 or not mt5.terminal_info():
            logger.warning("Close-position: MT5 not connected, cannot close ticket %d.", pos.ticket)
            return False
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        if not tick:
            logger.warning("Close-position: no tick for %s, cannot close ticket %d.", self.mt5_symbol, pos.ticket)
            return False
        if pos.type == mt5.POSITION_TYPE_BUY:
            close_type, close_price = mt5.POSITION_TYPE_SELL, tick.bid
        else:
            close_type, close_price = mt5.POSITION_TYPE_BUY, tick.ask
        base = {
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
        }
        for filling in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC):
            req = dict(base, type_filling=filling)
            with mt5_lock:
                res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info("Close-position: closed ticket %d (%.2f profit) — %s",
                            pos.ticket, pos.profit, reason)
                return True
            rc = getattr(res, "retcode", None)
            cm = getattr(res, "comment", "no result")
            logger.warning("Close-position: filling=%s failed for ticket %d (retcode=%s %s)",
                           filling, pos.ticket, rc, cm)
        return False

    def _current_known_sl(self, pos) -> float:
        """The stop we can TRUST the broker is holding for this position — our own
        shadow of the last SL we successfully set (seeded from the entry SL), NOT
        the raw pos.sl field.

        pos.sl comes from mt5.positions_get(), which the daemon calls UNLOCKED from
        several concurrent threads against the non-thread-safe MT5 client (only
        writes take mt5_lock). Under contention that read comes back torn — 0.0 or
        a stale value — for a position that genuinely holds a stop (confirmed live
        2026-08-14: broker held SL 159.006 while pos.sl read 0.0). Keying the
        monotonic ratchet off this shadow instead keeps a false 0/loose read from
        re-firing an identical modify or, worse, loosening the stop. We are the only
        writer of the SL (all modifies go through _modify_sl), so once seeded the
        shadow stays authoritative without re-reading the flaky field."""
        ticket = pos.ticket
        known = self._active_trade_sl.get(ticket)
        if known is not None:
            return known
        # First sight this session: seed from a live read only if it looks real
        # (>0), else from the recorded entry SL. Never seed 0 over a real entry
        # stop, so one torn first read cannot zero the baseline.
        live = pos.sl if (pos.sl and pos.sl > 0.0) else 0.0
        seed = live or (self._active_trade_initial_sl.get(ticket, 0.0) or 0.0)
        self._active_trade_sl[ticket] = seed
        return seed

    def _verify_broker_no_sl(self, ticket: int) -> bool:
        """Confirm a position REALLY has no stop before the trail establishes one.

        A lone unlocked positions_get() can falsely read sl==0 under cross-thread
        contention, so re-read the single ticket SERIALIZED under mt5_lock (the same
        lock that serializes writes) — a few times, bailing out the moment any read
        shows a real stop. Returns True only if every serialized read still shows
        sl<=0. If a real value surfaces it is adopted into the shadow (torn-read
        recovery) and we return False. Cannot-verify (error/None) also returns False
        so we never fabricate a stop off an unreadable state."""
        recovered = None
        for _ in range(3):
            try:
                with mt5_lock:
                    ps = mt5.positions_get(ticket=ticket)
            except Exception as e:
                logger.debug("no-SL verify re-read failed for %d: %s", ticket, e)
                return False
            if ps is None:
                return False
            if len(ps) == 0:
                # Position is gone (closed) — nothing to establish.
                return False
            sl = float(getattr(ps[0], "sl", 0.0) or 0.0)
            if sl > 0.0:
                recovered = sl
                break
        if recovered is not None:
            self._active_trade_sl[ticket] = recovered
            logger.debug("AxonDaemon: ticket %d SL re-read recovered %.5f (unlocked read was torn to 0)",
                         ticket, recovered)
            return False
        return True

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
                    exit_time_str = self._deal_time_local(exit_deal.time).strftime("%Y-%m-%d %H:%M:%S")
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
            self._maybe_engage_sl_lockout(reason, pips, profit)
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
                # MAE/MFE (max adverse/favorable excursion, pips) + hold time. Peak/worst
                # were tracked per-tick during the trade's life; fall back to entry_price
                # (0 excursion) if the trade closed before any trail-loop pass recorded them.
                _peak = self._active_trade_peak_price.get(ticket, entry_price)
                _worst = self._active_trade_worst_price.get(ticket, entry_price)
                _entry_t = self._active_trade_entry_time.get(ticket)
                _rt = self._retest_arm.get(ticket, {})
                if direction == "BUY":
                    _mfe = (_peak - entry_price) / pip
                    _mae = (entry_price - _worst) / pip
                else:
                    _mfe = (entry_price - _peak) / pip
                    _mae = (_worst - entry_price) / pip
                _hold_seconds = int(time.time() - _entry_t) if _entry_t else None
                # MFE early-exit shadow: whether the dead-fade cutoff would have
                # fired, and by how many pips it would have improved the realised result.
                _mx = self._mfe_exit_shadow.get(ticket, {})
                if _mx.get("fired"):
                    _mx = dict(_mx)
                    _mx["saved_pips"] = round(_mx["would_exit_pips"] - pips, 1)
                else:
                    _mx = {"fired": False}
                # Exit-capture shadow: what alternative exits WOULD have realised.
                #  • fixed_tp: cap at N pips → N if MFE reached N (price passed through
                #    it before any reversal), else the actual result. Improvement vs
                #    actual is (value − pips).  • alt_trail: the tighter-leash sim
                #    (per-tick, first shadow-stop hit); null-hit → the trade closed
                #    before the tight trail tripped, so it equals the actual result.
                _mfe_r = round(_mfe, 1)
                _tps = self.config.get("exit_shadow_tp_pips", [3.0, 4.0, 5.0, 6.0])
                _fixed_tp = {f"tp{int(t)}": (round(t, 1) if _mfe_r >= t else round(pips, 1))
                             for t in _tps}
                _es = self._exit_shadow.get(ticket, {})
                _alt_trail = {"mult": _es.get("mult", self.config.get("exit_shadow_trail_atr_mult", 0.35)),
                              "pips": _es.get("pips") if _es.get("hit") else round(pips, 1),
                              "hit": bool(_es.get("hit"))}
                # Give-back trail A/B (2026-08-14): counterfactual exit of a give-back trail
                # (exit_shadow_giveback_atr from peak, armed in profit) vs the live structure-trail.
                # null-hit -> trade closed before the give-back tripped, so it equals the actual.
                _gb = {"atr": self.config.get("exit_shadow_giveback_atr", 0.6),
                       "pips": _es.get("gb_pips") if _es.get("gb_hit") else round(pips, 1),
                       "hit": bool(_es.get("gb_hit"))}
                _exit_cap = {"actual_pips": round(pips, 1), "mfe_pips": _mfe_r,
                             "fixed_tp": _fixed_tp, "alt_trail": _alt_trail, "giveback": _gb}
                payload = {
                    "timestamp": exit_time_str,
                    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
                    "mae_pips": round(_mae, 1),
                    "mfe_pips": round(_mfe, 1),
                    "hold_seconds": _hold_seconds,
                    "retest_verdict": _rt.get("verdict"),
                    "retest_delay_sec": _rt.get("delay"),
                    "mfe_exit_shadow": _mx,
                    "exit_capture_shadow": _exit_cap,
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
            self._active_trade_sl.pop(ticket, None)
            self._active_trade_atr.pop(ticket, None)
            self._active_trade_peak_price.pop(ticket, None)
            self._active_trade_worst_price.pop(ticket, None)
            self._active_trade_sr_level.pop(ticket, None)
            if self._shadow_cut is not None:
                self._shadow_cut.forget(ticket)
            self._retest_arm.pop(ticket, None)
            self._stage_arm.pop(ticket, None)
            self._mfe_exit_shadow.pop(ticket, None)
            self._exit_shadow.pop(ticket, None)
            self._active_trade_entry_time.pop(ticket, None)
            
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
