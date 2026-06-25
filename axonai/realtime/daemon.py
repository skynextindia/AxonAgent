"""AxonAI real-time trading daemon.

Always-alive process that monitors MT5 tick data, detects
structural market events, and executes trades via pure-math
rule-based signals (no LLM / third-party AI dependencies).
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
from axonai.dataflows.mt5_order_bridge import send_order_via_bridge, start_bridge, stop_bridge
from axonai.realtime.event_types import EventPriority, LiveCandle, MarketEvent, EventType
from axonai.realtime.tick_engine import TickEngine
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.realtime.reversal_model import ReversalModel
from axonai.realtime.displacement_normalizer import DisplacementNormalizer
from axonai.realtime.trade_analytics import TradeAnalytics
from axonai.realtime.trade_executor import MT5TradeExecutor
from axonai.realtime.api_server import get_dashboard

logger = logging.getLogger(__name__)


class AxonDaemon:
    """Always-alive trading daemon.

    Lifecycle:
    1. Initialize MT5 connection
    2. Cold-start LiveWorldState + LiveMarketEvidence from historical bars
    3. Start TickEngine thread (Layer 1)
    4. Main loop: consume events from queue, execute trades
    5. On shutdown: gracefully stop threads, close MT5
    """

    def __init__(self, symbol: str, config: dict):
        clean_sym = symbol.replace("=X", "").replace("=x", "").strip()
        self.yf_symbol = clean_sym + "=X"  # e.g. "EURUSD=X"
        self.mt5_symbol = _to_mt5_symbol(symbol, config)
        self.config = config
        self._trade_terminal_path = config.get("mt5_trade_terminal_path")
        self.offset_hours = 0
        self.tz = timezone.utc
        self.event_queue: queue.Queue = queue.Queue(maxsize=100)
        self._running = False

        # Layer 1: Tick Engine
        self.tick_engine = TickEngine(self.mt5_symbol, config)

        # Layer 2: Live State + Reversal Engine
        self.live_state = LiveWorldState(symbol, config)
        self.live_evidence = LiveMarketEvidence(symbol, config)
        self.reversal_model = ReversalModel(
            pip_mult=0.0001 if "JPY" not in symbol.upper() else 0.01, 
            config=config
        )
        self.trade_analytics = TradeAnalytics()

        # Cooldown tracking (replaces graph_executor cooldown)
        self._cooldown_seconds: int = config.get("realtime_cooldown_seconds", 300)
        self._last_execution_time: datetime = datetime.min
        self._last_loss_time: Optional[datetime] = None
        self._last_close_time: Optional[datetime] = None

        # Layer 4: Trade Executor
        config_base = config.copy()
        config_base["realtime_magic_number"] = 123456
        self.trade_executor_base = MT5TradeExecutor(config_base)

        config_opt = config.copy()
        config_opt["realtime_magic_number"] = 123457
        self.trade_executor_opt = MT5TradeExecutor(config_opt)

        # Layer 5: Velocity-based Trailing Stops
        from axonai.realtime.velocity_trailing import VelocityTrailingManager
        self.velocity_trailing = VelocityTrailingManager()
        self._lowest_price_since_entry = {}  # Track lowest price for retest detection
        self._last_velocity_percentile = 0.0

        # Layer 6: Displacement Normalization
        self.displacement_normalizer = DisplacementNormalizer(window_sec=300.0)

        self.trade_executor = self.trade_executor_opt  # Default fallback reference

        # Trailing stop and trade outcome tracking
        self._tracked_positions: set[int] = set()
        self._active_trade_initial_sl: dict[int, float] = {}
        self._active_trade_system: dict[int, str] = {}

        # Stats
        self._events_detected: int = 0
        self._events_fired: int = 0
        self._events_skipped: int = 0
        self._start_time: Optional[datetime] = None
        self.paused: bool = False
        self._last_snapshot = None

        # CHANGE 9B: MT5 slow poll caches (keep off tick thread)
        self._account_info_cache = None
        self._position_cache = None
        self._slow_poll_running = True

        import threading

        self._slow_poll_thread = threading.Thread(
            target=self._slow_poll_loop, daemon=True, name="mt5-slow-poll"
        )
        self._slow_poll_thread.start()


    def _slow_poll_loop(self) -> None:
        """Poll slow MT5 endpoints every 1s via bridge. Never blocks tick thread."""
        from axonai.dataflows.mt5_order_bridge import get_account_info_via_bridge, get_positions_via_bridge
        poll_interval = self.config.get("dashboard_mt5_poll_interval_seconds", 1.0)
        first_run = True
        while self._slow_poll_running:
            try:
                if not self._trade_terminal_path:
                    if first_run:
                        logger.warning("SlowPollLoop: _trade_terminal_path is None/empty - account data unavailable")
                        first_run = False
                    time.sleep(poll_interval)
                    continue

                acc = get_account_info_via_bridge(self._trade_terminal_path)
                if acc:
                    if acc.get("success"):
                        self._account_info_cache = acc
                        logger.debug("SlowPollLoop: Account cached - balance=%.2f equity=%.2f", acc.get("balance", 0), acc.get("equity", 0))
                    else:
                        logger.warning("SlowPollLoop: Bridge returned success=False: %s", acc)
                else:
                    logger.warning("SlowPollLoop: get_account_info_via_bridge returned None")

                pos = get_positions_via_bridge(self._trade_terminal_path, self.mt5_symbol)
                if pos and pos.get("success"):
                    self._position_cache = pos.get("positions", [])
                    logger.debug("SlowPollLoop: Positions cached - count=%d", len(self._position_cache))
            except Exception as e:
                logger.warning(f"SlowPollLoop failed: {e}", exc_info=True)
            time.sleep(poll_interval)

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

    @staticmethod
    def _entry_direction(event) -> Optional[str]:
        """Map a peak event to a trade side. Returns 'BUY', 'SELL', or None.

        Single source of truth for entry direction (used by both the gate and
        the execution path). None means the side is indeterminate — callers
        MUST fail closed (skip the trade), never default to a side.
        """
        dir_str = (event.details.get("direction") or "").lower()
        if "bullish" in dir_str:
            return "BUY"
        if "bearish" in dir_str:
            return "SELL"
        return None

    def _get_mode_payload(self) -> dict:
        """Describe the daemon's execution mode for the dashboard badge."""
        paper = bool(self.config.get("paper_trade", False))
        dry_run = bool(self.config.get("realtime_dry_run", False))
        if paper:
            label, color = "PAPER", "cyan"          # simulated fills, nothing sent
        elif dry_run:
            label, color = "DRY-RUN LIVE", "amber"   # real demo orders sent to MT5
        else:
            label, color = "LIVE", "red"             # real orders, real account
        return {
            "type": "mode",
            "label": label,
            "color": color,
            "paper": paper,
            "dry_run": dry_run,
            "llm": False,
            "engine": "Pure-Math (Rule A+B)",
        }

    def _get_regime_payload(self) -> dict:
        ws = self.live_state.snapshot()
        me = self.live_evidence.snapshot()
        
        entry_state = "IDLE"
        entry_direction = None
        entry_reason = "Awaiting anomaly"
        entry_quality = 0.0
        health_score = 1.0
        health_active = False
        health_reason = "Healthy"
        exit_action = "HOLD"
        exit_reason = ""
        exit_should_exit = False
        
        if getattr(self, "_last_snapshot", None) is not None:
            snap = self._last_snapshot
            entry_state = snap.entry_decision.state
            entry_direction = snap.entry_decision.direction
            entry_reason = snap.entry_decision.reason
            entry_quality = snap.entry_decision.signal_quality
            health_score = snap.trade_health.score
            health_active = self.reversal_model.health._is_active
            health_reason = snap.trade_health.reason
            exit_action = snap.exit_decision.action
            exit_reason = snap.exit_decision.reason
            exit_should_exit = snap.exit_decision.should_exit

        
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

        regime_msg = {
            "type": "regime",
            "symbol": self.mt5_symbol,
            "dominant": ws.dominant_regime,
            "confidence": ws.regime_confidence,
            "volatility": ws.volatility_regime,
            "atr": self.reversal_model._h1_atr,
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
            "cooldown_remaining": int(self._seconds_until_ready()),
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
            # Pure-math gate metrics
            "gate_status": {
                "state_passed": ws.belief_score > 0.3,
                "spread_passed": ws.spread_safe,
                "conviction_passed": ws.belief_score >= 0.65,
                "rate_limit_passed": self._seconds_until_ready() == 0,
                "context_passed": ws.abort_reason is None or ws.abort_reason == "",
                "llm_paused": getattr(self, "paused", False)
            },
            # Entry State Machine & Execution telemetry
            "entry_state": entry_state,
            "entry_direction": entry_direction,
            "entry_reason": entry_reason,
            "entry_quality": entry_quality,
            "health_score": health_score,
            "health_active": health_active,
            "health_reason": health_reason,
            "exit_action": exit_action,
            "exit_reason": exit_reason,
            "exit_should_exit": exit_should_exit,
            # Map legacy token tracking keys to pure-math parameters for UI dashboard compat
            "tokens_in": round(ws.displacement.displacement_ratio, 2) if (ws and getattr(ws, "displacement", None)) else 0.0,
            "tokens_out": round(ws.velocity.tick_efficiency, 2) if (ws and getattr(ws, "velocity", None)) else 0.0,
            "tokens_total": round(ws.velocity.raw_velocity, 2) if (ws and getattr(ws, "velocity", None)) else 0.0,
            "llm_calls": entry_state,
            "tool_calls": entry_direction or "WAIT"
        }

        return regime_msg

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
        # Validate timeframe to prevent crashes from invalid input
        valid_timeframes = {"M15", "H1", "H4"}
        if timeframe not in valid_timeframes:
            logger.warning("AxonDaemon: Invalid timeframe '%s', using M15 as fallback", timeframe)
            timeframe = "M15"

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

    def _send_order(self, request: dict) -> Optional[dict]:
        """Send an order via the bridge subprocess."""
        from axonai.dataflows.mt5_order_bridge import send_order_via_bridge
        if not self._trade_terminal_path:
            logger.error("Trade terminal path not configured")
            return None
        return send_order_via_bridge(self._trade_terminal_path, request)

    def _get_account_payload(self) -> Optional[dict]:
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        if is_bridge:
            from axonai.realtime.execution_client import send_execution_command
            try:
                acc_res = send_execution_command(self.config, {"action": "account_info"})
                if not acc_res.get("success", False):
                    logger.debug("Bridge execution_client returned success=False")
                    return None
                pos_res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol})
                pos_list = pos_res.get("positions", [])
                return {
                    "type": "account",
                    "balance": acc_res.get("balance", 0.0),
                    "equity": acc_res.get("equity", 0.0),
                    "profit": acc_res.get("profit", 0.0),
                    "margin": acc_res.get("margin", 0.0),
                    "free_margin": acc_res.get("free_margin", 0.0),
                    "margin_level": acc_res.get("margin_level", 0.0),
                    "positions": pos_list
                }
            except Exception as e:
                logger.warning("Failed to retrieve execution bridge account info: %s", e)
                return None

        # Use cached data from _slow_poll_loop instead of making fresh bridge calls
        if self._account_info_cache and isinstance(self._account_info_cache, dict):
            acc = self._account_info_cache
            pos_list = []
            if self._position_cache:
                for p in self._position_cache:
                    if isinstance(p, dict):
                        pos_list.append({
                            "ticket": int(p.get("ticket", 0)),
                            "symbol": p.get("symbol", ""),
                            "type": p.get("type", "BUY"),
                            "volume": float(p.get("volume", 0)),
                            "price_open": float(p.get("price_open", 0)),
                            "price_current": float(p.get("price_current", 0)),
                            "sl": float(p.get("sl", 0)),
                            "tp": float(p.get("tp", 0)),
                            "profit": float(p.get("profit", 0))
                        })

            payload = {
                "type": "account",
                "balance": acc.get("balance", 0),
                "equity": acc.get("equity", 0),
                "profit": acc.get("profit", 0),
                "margin": acc.get("margin", 0),
                "free_margin": acc.get("margin_free", 0),
                "margin_level": acc.get("margin_level", 0.0),
                "positions": pos_list
            }
            logger.debug("Account payload from cache: balance=%.2f equity=%.2f profit=%.2f", payload["balance"], payload["equity"], payload["profit"])
            return payload

        # Cache is empty - log diagnostic info
        logger.warning("_get_account_payload: Cache empty. is_bridge=%s, cache=%s, trade_terminal_path=%s",
                      is_bridge, self._account_info_cache is not None, self._trade_terminal_path)
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

        # 1. Initialize MT5 (data feed - Exness)
        if not mt5_initialize(self.config.get("mt5_terminal_path")):
            logger.error("AxonDaemon: MT5 initialization failed. Cannot start.")
            return
        logger.info("Step 1/4: MT5 data feed connected")

        # 1B. Start order bridge for trade execution (separate subprocess to keep Exness data feed)
        trade_terminal_path = self.config.get("mt5_trade_terminal_path")
        self._trade_terminal_path = trade_terminal_path
        if trade_terminal_path and start_bridge(trade_terminal_path):
            logger.info("Step 1B/4: MT5 order bridge started (dual-terminal mode via subprocess)")

        # Pre-populate active positions for trailing SL tracking
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        try:
            if is_bridge:
                from axonai.realtime.execution_client import send_execution_command
                res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol})
                positions = res.get("positions", []) if res.get("success", False) else []
                if positions:
                    for pos in positions:
                        ticket = pos["ticket"]
                        self._tracked_positions.add(ticket)
                        self._active_trade_initial_sl[ticket] = pos["sl"]
                    logger.info("AxonDaemon: Pre-populated %d active positions from execution bridge.", len(positions))
            else:
                from axonai.dataflows.mt5_order_bridge import get_positions_via_bridge
                if self._trade_terminal_path:
                    pos_result = get_positions_via_bridge(self._trade_terminal_path, self.mt5_symbol)
                    positions = pos_result.get("positions", []) if pos_result and pos_result.get("success") else []
                    if positions:
                        for pos in positions:
                            self._tracked_positions.add(pos["ticket"])
                            self._active_trade_initial_sl[pos["ticket"]] = pos["sl"]
                        logger.info("AxonDaemon: Pre-populated %d active positions for trailing stop tracking.", len(positions))
        except Exception as pe:
            logger.warning("AxonDaemon: Failed to pre-populate active positions: %s", pe)

        # Broadcast initial account data so newly-connected browsers get it immediately
        time.sleep(1.5)  # Wait for slow_poll_loop to populate cache
        dashboard = get_dashboard()
        if dashboard:
            acc_payload = self._get_account_payload()
            if acc_payload:
                dashboard.broadcast(acc_payload)
                logger.info("Broadcasted initial account data on startup")

        # Now that MT5 is connected, re-resolve the broker symbol via live
        # auto-detection. In __init__ MT5 wasn't initialized yet, so the suffix
        # fell back to the configured default (e.g. "m") and could be wrong for
        # this broker. Re-running now lets _to_mt5_symbol query symbol_info().
        from axonai.dataflows.mt5_data import _ensure_symbol_visible, _to_mt5_symbol
        resolved = _to_mt5_symbol(self.yf_symbol, self.config)
        if resolved != self.mt5_symbol:
            logger.info("AxonDaemon: re-resolved broker symbol %s -> %s after MT5 connect", self.mt5_symbol, resolved)
            self.mt5_symbol = resolved
            self.tick_engine.symbol = resolved
        _ensure_symbol_visible(self.mt5_symbol)
        self.offset_hours = get_broker_tz_offset(self.mt5_symbol)
        self.tz = timezone(timedelta(hours=self.offset_hours))
        logger.info("Step 1/4: Broker timezone offset detected: %d hours", self.offset_hours)

        # 2. Cold-start state from historical bars
        logger.info("Step 2/4: Cold-starting live state...")
        self.live_state.initialize()
        self.live_evidence.initialize()

        # Sync initial detected levels into reversal model
        self.reversal_model.sync_levels(self.live_evidence.price_levels)
        logger.info("Step 2/4: Live state initialized")

        # 3. Pure-math engine (no LLM graph)
        logger.info("Step 3/4: Engine mode: Pure-Math Rule A+B signals (no LLM)")

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
            # 0. Execution mode badge (paper / dry-run / live + LLM on/off)
            dashboard.broadcast(self._get_mode_payload())

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
        # TEST TRIGGER: optionally queue a mock event immediately to demo the debate.
        # Disabled by default — enable only for demos via realtime_inject_test_event.
        if self.config.get("realtime_inject_test_event", False):
            from axonai.realtime.event_types import MarketEvent, EventType, EventPriority
            logger.warning("realtime_inject_test_event is ON — injecting a mock LEVEL_BREACH event")
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
        mid = (bid + ask) / 2.0
        self.live_state.on_tick(bid, ask, timestamp)
        self.live_evidence.on_tick(bid, ask, timestamp, volume)
        self.reversal_model.sync_levels(self.live_evidence.price_levels)

        # CHANGE 9A: Compute location context ONCE here, pass to reversal_model
        location_context = self.reversal_model.location_engine.compute(
            price=mid,
            atr_14_h1=self.reversal_model._h1_atr,
            recent_candles=self.live_evidence._m1_candles[-50:]
            if hasattr(self.live_evidence, "_m1_candles")
            else [],
            price_levels=self.live_evidence.price_levels,
        )

        # 1. Update pure-math reversal engine
        _t0 = time.perf_counter()
        snapshot = self.reversal_model.on_tick(
            mid, timestamp, volume, location_context=location_context,
            displacement_normalizer=self.displacement_normalizer
        )
        _t1 = time.perf_counter()
        self._last_snapshot = snapshot

        # CHANGE 9C: Dependency injection for EntryDecision
        if snapshot and snapshot.entry_decision:
            snapshot.entry_decision.entry_location_context = {
                "distance_to_liquidity": location_context.distance_to_liquidity,
                "distance_to_sr": location_context.distance_to_sr,
                "room_available": location_context.room_available,
                "at_structure": location_context.at_structure,
                "nearest_level_type": location_context.nearest_level_type,
                "nearest_level_price": location_context.nearest_level_price,
            }
            if snapshot.regime:
                snapshot.entry_decision.entry_regime = snapshot.regime.regime
            if snapshot.velocity:
                snapshot.entry_decision.entry_velocity_percentile = snapshot.velocity.percentile
            if snapshot.displacement:
                snapshot.entry_decision.entry_displacement_class = snapshot.displacement.classification

        # 2. Check for entry triggers + broadcast trigger metrics to dashboard
        dashboard = get_dashboard()
        if dashboard:
            # Broadcast trigger metrics for real-time visualization
            logger.debug(f"Broadcasting trigger_metrics: state={snapshot.entry_decision.state} disp_class={snapshot.displacement.classification}")
            dashboard.broadcast({
                "type": "trigger_metrics",
                "state": snapshot.entry_decision.state,
                "direction": snapshot.entry_decision.direction,
                "signal_quality": snapshot.entry_decision.signal_quality,
                "is_valid": snapshot.entry_decision.is_valid_entry,
                "reason": snapshot.entry_decision.reason,
                # Displacement metrics
                "displacement_class": snapshot.displacement.classification,
                "displacement_ratio": round(snapshot.displacement.displacement_ratio, 3),
                "velocity_z_score": round(snapshot.velocity.z_score, 2),
                "velocity_is_unusual": snapshot.velocity.is_unusual,
                "velocity_tick_efficiency": round(snapshot.velocity.tick_efficiency, 3),
                # Market context
                "regime": snapshot.regime.regime if snapshot.regime else "UNKNOWN",
                "mtf_h1_bias": round(snapshot.mtf.h1_bias, 2) if snapshot.mtf else 0.0,
                "mtf_h4_bias": round(snapshot.mtf.h4_bias, 2) if snapshot.mtf else 0.0,
            })

        if snapshot.entry_decision.is_valid_entry:
            self.event_queue.put({"type": "entry", "snapshot": snapshot})

        # 3. Check for exit triggers
        if snapshot.exit_decision.should_exit or snapshot.exit_decision.action == "ADJUST_SL":
            self.event_queue.put({"type": "exit", "snapshot": snapshot})

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
                # 1. Check for tick efficiency collapse (Price is moving fast but not going anywhere)
                eff = snapshot.velocity.tick_efficiency if 'snapshot' in locals() else 1.0
                collapse = (eff < 0.15) and (velocity > 1.5)

                # 2. Check for aggression shift (Sudden reversal in order flow dominance)
                i60 = imb.get("imbalance_60s", 0.0)
                i10 = imb.get("imbalance_10s", 0.0)
                agg_shift = (i60 > 0.4 and i10 < -0.4) or (i60 < -0.4 and i10 > 0.4)

                # 3. Check for absorption (High volume, high velocity, but zero displacement)
                t_30s = [t for t in ticks if (ticks[-1]['time'] - t['time']).total_seconds() <= 30.0]
                pip_unit = 0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001
                absorption = len(t_30s) >= 20 and velocity > 1.5 and abs(t_30s[-1]['mid'] - t_30s[0]['mid']) < (2.0 * pip_unit)

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
                
                # Rule A & B Live Stats from Tier 1
                "rule_b_divergence": snapshot.displacement.displacement_ratio,
                "rule_b_efficiency": snapshot.velocity.tick_efficiency,
                "rule_b_confirmed": snapshot.entry_decision.state == "TRIGGERED",
                "rule_a_max_vel": snapshot.velocity.raw_velocity,
                "rule_a_avg_vel": snapshot.velocity.abs_velocity,
                
                # Entry State Machine & Execution telemetry
                "entry_state": snapshot.entry_decision.state,
                "entry_direction": snapshot.entry_decision.direction,
                "entry_reason": snapshot.entry_decision.reason,
                "entry_quality": snapshot.entry_decision.signal_quality,
                "health_score": snapshot.trade_health.score,
                "health_active": self.reversal_model.health._is_active,
                "health_reason": snapshot.trade_health.reason,
                "exit_action": snapshot.exit_decision.action,
                "exit_reason": snapshot.exit_decision.reason,
                "exit_should_exit": snapshot.exit_decision.should_exit
            })

            # CHANGE 10C: Broadcast trade_state and location_context panels
            trade_state = self.reversal_model.trade_state_engine.get_state()
            if trade_state:
                dashboard.broadcast({
                    "type": "trade_state",
                    "ticket": trade_state.ticket,
                    "current_phase": trade_state.current_phase,
                    "health_score": trade_state.health_score,
                    "mfe": trade_state.mfe,
                    "mae": trade_state.mae,
                    "thesis_status": trade_state.thesis_status,
                    "current_profit_pips": trade_state.current_profit_pips,
                    "ticks_in_trade": trade_state.ticks_in_trade,
                })

                if trade_state.location_context:
                    dashboard.broadcast({
                        "type": "location_context",
                        "distance_to_sr": trade_state.location_context.get("distance_to_sr", 0.0),
                        "distance_to_liquidity": trade_state.location_context.get("distance_to_liquidity", 0.0),
                        "room_available": trade_state.location_context.get("room_available", 0.0),
                        "at_structure": trade_state.location_context.get("at_structure", False),
                        "nearest_level_type": trade_state.location_context.get("nearest_level_type", ""),
                        "nearest_level_price": trade_state.location_context.get("nearest_level_price", 0.0),
                    })

            # CHANGE 9D: Timing instrumentation
            _t2 = time.perf_counter()
            if self.config.get("latency_instrumentation_enabled", False):
                _rev_ms = (_t1 - _t0) * 1000
                _brd_ms = (_t2 - _t1) * 1000
                _tot_ms = (_t2 - _t0) * 1000
                if _tot_ms > 10.0:
                    logger.error(
                        f"on_tick exceeded budget: {_tot_ms:.1f}ms "
                        f"(reversal={_rev_ms:.1f}ms, broadcast={_brd_ms:.1f}ms)"
                    )
                elif _tot_ms > 5.0:
                    logger.warning(f"on_tick slow: {_tot_ms:.1f}ms")

            # Throttle heavier updates to once every 5 ticks
            if self.tick_engine._tick_count % 5 == 1:
                dashboard.broadcast(self._get_regime_payload())
                
                # Fetch and broadcast MetaTrader 5 account info
                acc_payload = self._get_account_payload()
                if acc_payload:
                    logger.info("Broadcasting account: balance=%.2f equity=%.2f profit=%.2f",
                               acc_payload.get("balance", 0),
                               acc_payload.get("equity", 0),
                               acc_payload.get("profit", 0))
                    dashboard.broadcast(acc_payload)
                else:
                    logger.debug("Account payload is None")

    def _on_candle_close(self, candle: LiveCandle):
        """Called by TickEngine when any timeframe candle closes."""
        self.live_state.on_candle_close(candle)
        self.live_evidence.on_candle_close(candle)
        self.reversal_model.sync_levels(self.live_evidence.price_levels)
        self.reversal_model.on_candle_close(candle)
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

            # Determine event type
            if not isinstance(event, dict) or "type" not in event or "snapshot" not in event:
                continue

            event_type = event["type"]
            snapshot = event["snapshot"]
            
            # Throttle logging
            if event_type == "entry":
                self._events_detected += 1
                
                # Check if trading is paused
                if getattr(self, "paused", False):
                    self._events_skipped += 1
                    logger.info("SKIPPED (Trading operations PAUSED)")
                    continue
                
                # Check cooldown
                remaining = self._seconds_until_ready()
                if remaining > 0:
                    self._events_skipped += 1
                    logger.info("SKIPPED (cooldown=%.0fs remaining)", remaining)
                    continue

                signal = "Buy" if snapshot.entry_decision.direction == "BUY" else "Sell"
                system_name = "reversal_model"
                logger.info("EXECUTING: ReversalModel Triggered → signal: %s", signal)

                # Broadcast decision status
                dashboard = get_dashboard()
                if dashboard:
                    dashboard.broadcast({
                        "type": "decision",
                        "signal": signal,
                        "system": system_name,
                        "paper": bool(self.config.get("paper_trade", False)),
                        "engine": "pure-math",
                        "quality": snapshot.entry_decision.signal_quality,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })

                # Execute order on MT5 terminal
                trade_result = None
                try:
                    trade_result = self.trade_executor_opt.execute_signal(self.mt5_symbol, signal, self.live_state)
                    if trade_result and trade_result.get("success", True) and trade_result.get("order"):
                        logger.info("AxonDaemon: Order execution complete: %s", trade_result)
                        ticket = trade_result.get("order")
                        self._tracked_positions.add(ticket)
                        self._active_trade_initial_sl[ticket] = trade_result.get("sl")
                        self._active_trade_system[ticket] = system_name
                        
                        # Register trade with models
                        # CHANGE 9C: Register with trade_state_engine for lifecycle tracking
                        self.reversal_model.trade_state_engine.register_trade(
                            ticket=ticket,
                            direction=snapshot.entry_decision.direction,
                            entry_price=snapshot.price,
                            entry_time=datetime.now(timezone.utc),
                            entry_sl=trade_result.get("sl"),
                            entry_tp=trade_result.get("tp"),
                            entry_reason=snapshot.entry_decision.reason,
                            position_size=trade_result.get("volume", 0.01),
                        )

                        # Also keep legacy register_trade for health monitoring compatibility
                        self.reversal_model.register_trade(
                            ticket, snapshot.entry_decision.direction, snapshot.price,
                            trade_result.get("sl"), trade_result.get("tp"),
                            reason=snapshot.entry_decision.reason
                        )
                        self.trade_analytics.record_entry(
                            ticket, self.mt5_symbol, snapshot.entry_decision.direction,
                            snapshot.price, trade_result.get("sl"), trade_result.get("tp"), snapshot
                        )
                except Exception as ex_err:
                    logger.error("AxonDaemon: Trade execution error: %s", ex_err, exc_info=True)

                self._last_execution_time = datetime.now()
                self._events_fired += 1
                
                # We can mock a log here if needed
                if hasattr(self, '_log_dry_run_event'):
                    self._log_dry_run_event('event_detected', {'event_type': 'REVERSAL', 'price': snapshot.price, 'details': {}})

            elif event_type == "exit":
                # CHANGE 9C: Position reconciliation
                is_live = self.reversal_model.trade_state_engine.is_position_live()
                if not is_live:
                    logger.warning(
                        "EXIT signal received but no live position found. "
                        "Skipping exit to prevent ghost close."
                    )
                    continue

                # The exit action could be ADJUST_SL or CLOSE_NOW
                decision = snapshot.exit_decision
                logger.info("EXIT DECISION: %s - %s", decision.action, decision.reason)
                
                is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
                if decision.action == "ADJUST_SL" and decision.suggested_sl:
                    # Trailing stop update logic
                    if is_bridge:
                        from axonai.realtime.execution_client import send_execution_command
                        res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol, "magic": self.trade_executor_opt.magic})
                        positions = res.get("positions", []) if res.get("success", False) else []
                        for p in positions:
                            send_execution_command(self.config, {
                                "action": "modify",
                                "position": p["ticket"],
                                "symbol": p["symbol"],
                                "sl": decision.suggested_sl,
                                "tp": p["tp"]
                            })
                            logger.info("Adjusted SL (Bridge) for ticket %d to %.5f", p["ticket"], decision.suggested_sl)
                    else:
                        from axonai.dataflows.mt5_order_bridge import get_positions_via_bridge
                        if self._trade_terminal_path:
                            pos_result = get_positions_via_bridge(self._trade_terminal_path, self.mt5_symbol)
                            positions = pos_result.get("positions", []) if pos_result and pos_result.get("success") else []
                        else:
                            positions = []
                        if positions:
                            for p in positions:
                                request = {
                                    "action": mt5.TRADE_ACTION_SLTP,
                                    "position": p["ticket"],
                                    "symbol": p["symbol"],
                                    "sl": decision.suggested_sl,
                                    "tp": p["tp"]
                                }
                                self._send_order(request)
                                logger.info("Adjusted SL for ticket %d to %.5f", p["ticket"], decision.suggested_sl)
                                        
                elif decision.action == "CLOSE_NOW":
                    if is_bridge:
                        from axonai.realtime.execution_client import send_execution_command
                        res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol, "magic": self.trade_executor_opt.magic})
                        positions = res.get("positions", []) if res.get("success", False) else []
                        for p in positions:
                            # Close position via bridge
                            # Determine close side (SELL to close BUY, BUY to close SELL)
                            order_type = 1 if p["type"] == "BUY" else 0
                            tick_bid = self.live_state.current_bid if hasattr(self.live_state, "current_bid") else p["price_current"]
                            tick_ask = self.live_state.current_ask if hasattr(self.live_state, "current_ask") else p["price_current"]
                            price = tick_ask if order_type == 0 else tick_bid
                            
                            close_res = send_execution_command(self.config, {
                                "action": "close",
                                "position": p["ticket"],
                                "symbol": p["symbol"],
                                "volume": p["volume"],
                                "type": order_type,
                                "price": price,
                                "magic": p["magic"],
                                "deviation": 20
                            })
                            if close_res.get("success"):
                                profit_pips = (price - p["price_open"]) / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001)
                                if p["type"] == "SELL": profit_pips = -profit_pips
                                
                                self.trade_analytics.record_exit(
                                    p["ticket"], price, profit_pips, decision.reason, snapshot
                                )
                                self.reversal_model.clear_trade()
                                if p["ticket"] in self._tracked_positions:
                                    self._tracked_positions.remove(p["ticket"])
                                    
                                logger.info("Successfully closed position %d via bridge: %s", p["ticket"], decision.reason)
                    else:
                        from axonai.dataflows.mt5_order_bridge import get_positions_via_bridge
                        if self._trade_terminal_path:
                            pos_result = get_positions_via_bridge(self._trade_terminal_path, self.mt5_symbol)
                            positions = pos_result.get("positions", []) if pos_result and pos_result.get("success") else []
                        else:
                            positions = []
                        if positions:
                            for p in positions:
                                # Create market order to close
                                tick = mt5.symbol_info_tick(self.mt5_symbol)
                                price = tick.ask if p["type"] == "SELL" else tick.bid
                                order_type = 1 if p["type"] == "SELL" else 0  # BUY=1, SELL=0
                                request = {
                                    "action": mt5.TRADE_ACTION_DEAL,
                                    "symbol": p["symbol"],
                                    "volume": p["volume"],
                                    "type": order_type,
                                    "position": p["ticket"],
                                    "price": price,
                                    "deviation": 20,
                                    "magic": self.trade_executor_opt.magic,
                                    "comment": f"Adaptive Exit: {decision.reason}",
                                    "type_time": mt5.ORDER_TIME_GTC,
                                    "type_filling": mt5.ORDER_FILLING_IOC,
                                }
                                res = self._send_order(request)
                                if res and res.get("retcode") == mt5.TRADE_RETCODE_DONE:
                                            profit_pips = (price - p.price_open) / (0.01 if "JPY" in self.mt5_symbol.upper() else 0.0001)
                                            if p.type == mt5.POSITION_TYPE_SELL: profit_pips = -profit_pips
                                            
                                            self.trade_analytics.record_exit(
                                                p.ticket, price, profit_pips, decision.reason, snapshot
                                            )
                                            self.reversal_model.clear_trade()
                                            if p.ticket in self._tracked_positions:
                                                self._tracked_positions.remove(p.ticket)
                                                
                                            logger.info("Successfully closed position %d: %s", p.ticket, decision.reason)

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

    def _seconds_until_ready(self) -> float:
        """Seconds remaining in execution cooldown."""
        now = datetime.now()
        
        # Standard entry cooldown
        cooldown_seconds = self.config.get("realtime_cooldown_seconds", self.config.get("cooldown_seconds", 300))
        elapsed = (now - self._last_execution_time).total_seconds()
        cooldown_rem = max(0.0, cooldown_seconds - elapsed)
        
        # Post-trade cooldown (measured from last close/exit time)
        post_trade_rem = 0.0
        if getattr(self, "_last_close_time", None) is not None:
            elapsed_close = (now - self._last_close_time).total_seconds()
            post_trade_rem = max(0.0, self._cooldown_seconds - elapsed_close)
            
        # Loss cooldown check
        loss_cooldown_rem = 0.0
        if getattr(self, "_last_loss_time", None) is not None:
            elapsed_loss = (now - self._last_loss_time).total_seconds()
            loss_cooldown_minutes = self.config.get("realtime_loss_cooldown_minutes", self.config.get("loss_cooldown_minutes", 30))
            loss_cooldown_rem = max(0.0, (loss_cooldown_minutes * 60) - elapsed_loss)
            
        return max(cooldown_rem, post_trade_rem, loss_cooldown_rem)

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
                self._seconds_until_ready(),
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
        stop_bridge()
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
        """Manage velocity-based trailing stops on active MT5 positions."""
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"

        # Throttling
        if is_bridge:
            if not hasattr(self, "_last_bridge_check_time"):
                self._last_bridge_check_time = 0.0
            import time as pytime
            now = pytime.time()
            if now - self._last_bridge_check_time < 1.0:
                return
            self._last_bridge_check_time = now

            if not self._tracked_positions:
                return

            from axonai.realtime.execution_client import send_execution_command
            res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol})
            positions = res.get("positions", []) if res.get("success", False) else []
        else:
            if not mt5 or not mt5.terminal_info():
                return
            positions = mt5.positions_get(symbol=self.mt5_symbol)

        if not positions:
            return

        pip = 0.01 if "JPY" in self.mt5_symbol.upper() or "XAU" in self.mt5_symbol.upper() else 0.0001

        # Get current market metrics for velocity trailing
        vel = self.reversal_model._last_vel_state if hasattr(self, 'reversal_model') else None
        disp = self.reversal_model._last_disp_state if hasattr(self, 'reversal_model') else None
        health_state = self.reversal_model.health if hasattr(self, 'reversal_model') else None

        for pos in positions:
            if is_bridge:
                ticket = pos["ticket"]
                pos_type = pos["type"]
                price_open = pos["price_open"]
                pos_sl = pos["sl"]
                pos_tp = pos["tp"]
                pos_symbol = pos["symbol"]
            else:
                ticket = pos.ticket
                pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                price_open = pos.price_open
                pos_sl = pos.sl
                pos_tp = pos.tp
                pos_symbol = pos.symbol

            # Initialize tracking
            if ticket not in self._active_trade_initial_sl:
                self._active_trade_initial_sl[ticket] = pos_sl
                self._tracked_positions.add(ticket)

            initial_sl = self._active_trade_initial_sl[ticket]
            if initial_sl <= 0.0:
                continue

            # Get velocity trailing decision
            pos_type_str = "BUY" if pos_type == 0 or pos_type == "BUY" else "SELL"

            # Calculate velocity acceleration (current vs previous)
            velocity_accel = 1.0
            if hasattr(self, '_last_velocity_percentile'):
                if self._last_velocity_percentile > 0:
                    velocity_accel = (vel.percentile / self._last_velocity_percentile) if vel else 1.0
            if vel:
                self._last_velocity_percentile = vel.percentile

            # Track lowest price for retest detection
            if ticket not in self._lowest_price_since_entry:
                self._lowest_price_since_entry[ticket] = price_open
            else:
                if pos_type_str == "BUY":
                    self._lowest_price_since_entry[ticket] = min(self._lowest_price_since_entry[ticket], bid)
                else:
                    self._lowest_price_since_entry[ticket] = max(self._lowest_price_since_entry[ticket], ask)

            trail_result = self.velocity_trailing.on_tick(
                ticket=ticket,
                bid=bid,
                ask=ask,
                position_type=pos_type_str,
                entry_price=price_open,
                initial_sl=initial_sl,
                current_sl=pos_sl,
                velocity_percentile=vel.percentile if vel else 50.0,
                velocity_acceleration=velocity_accel,
                displacement_ratio=disp.displacement_ratio if disp else 0.0,
                health_score=health_state.health_score if health_state else 50.0,
                at_structure=False,
                lowest_price=self._lowest_price_since_entry.get(ticket, price_open)
            )

            # Apply SL modification if velocity trailing suggests it
            if trail_result:
                new_sl = round(trail_result["new_sl"], 5 if "JPY" not in self.mt5_symbol.upper() else 3)
                logger.info(
                    "AxonDaemon: Velocity trail triggered for ticket %d. SL: %.5f -> %.5f (agg=%.2f)",
                    ticket, pos_sl, new_sl, trail_result["aggressiveness"]
                )

                if is_bridge:
                    from axonai.realtime.execution_client import send_execution_command
                    send_execution_command(self.config, {
                        "action": "modify",
                        "position": ticket,
                        "symbol": pos_symbol,
                        "sl": new_sl,
                        "tp": pos_tp,
                    })
                else:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": ticket,
                        "symbol": self.mt5_symbol,
                        "sl": new_sl,
                        "tp": pos_tp,
                    }
                    res = self._send_order(request)
                    if res and res.get("retcode") == mt5.TRADE_RETCODE_DONE:
                        logger.info("AxonDaemon: SL modification successful for ticket %d", ticket)

    def _check_for_closed_positions(self, bid: float, ask: float):
        """Detect closed positions and log outcomes."""
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        
        if is_bridge:
            if not self._tracked_positions:
                return
            from axonai.realtime.execution_client import send_execution_command
            res = send_execution_command(self.config, {"action": "positions_get", "symbol": self.mt5_symbol})
            positions = res.get("positions", []) if res.get("success", False) else []
            active_tickets = {int(p["ticket"]) for p in positions}
        else:
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
            if is_bridge:
                from axonai.realtime.execution_client import send_execution_command
                hist_res = send_execution_command(self.config, {"action": "history_deals_get", "position": ticket})
                deals = hist_res.get("deals", []) if hist_res.get("success", False) else []
            else:
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
                # Find exit deal
                exit_deal = None
                entry_deal = None
                for deal in deals:
                    deal_entry = deal["entry"] if is_bridge else deal.entry
                    
                    if deal_entry == 1 or (not is_bridge and deal_entry == mt5.DEAL_ENTRY_OUT):
                        exit_deal = deal
                    elif deal_entry == 0 or (not is_bridge and deal_entry == mt5.DEAL_ENTRY_IN):
                        entry_deal = deal
                        
                if entry_deal:
                    entry_price = entry_deal["price"] if is_bridge else entry_deal.price
                    volume = entry_deal["volume"] if is_bridge else entry_deal.volume
                    deal_type = entry_deal["type"] if is_bridge else entry_deal.type
                    direction = "BUY" if deal_type == 0 else "SELL"
                    
                if exit_deal:
                    exit_price = exit_deal["price"] if is_bridge else exit_deal.price
                    deal_time = exit_deal["time"] if is_bridge else exit_deal.time
                    exit_time_str = datetime.fromtimestamp(deal_time).strftime("%Y-%m-%d %H:%M:%S")
                    profit = exit_deal["profit"] if is_bridge else exit_deal.profit
                    comment = (exit_deal["comment"] if is_bridge else getattr(exit_deal, "comment", "")).lower()
                    
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
                        reason = f"Closed ({exit_deal['comment'] if is_bridge else (exit_deal.comment or 'Manual')})"
                        
            # If history failed, fallback to basic estimates
            if entry_price == 0.0:
                entry_price = bid  # fallback
                
            outcome = "WIN" if pips > 0 else "LOSS" if pips < 0 else "BREAKEVEN"
            if outcome == "LOSS":
                self._last_loss_time = datetime.now()
            
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
            self.velocity_trailing.reset(ticket)  # Clean up velocity trail state
            self._lowest_price_since_entry.pop(ticket, None)  # Clean up lowest price tracking
            
            # Apply post-trade global cooldown to prevent immediate reversal trades
            # caused by our own TP/SL orders hitting the market and causing a tick climax
            cooldown_minutes = 45 if profit < 0 else 15
            logger.info("Trade closed (Profit: %.2f). Applying %d minute post-trade cooldown.", profit, cooldown_minutes)
            self._cooldown_seconds = cooldown_minutes * 60
            self._last_close_time = datetime.now()
            if profit < 0:
                self._last_loss_time = datetime.now()
            
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
    events_detected = 0
    decisions = 0
    errors = 0

    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line)
                dt = datetime.fromisoformat(entry['timestamp'])
                if first_time is None: first_time = dt
                last_time = dt

                etype = entry['event_type']
                if etype == 'error':
                    errors += 1
                elif etype == 'decision':
                    decisions += 1
                elif etype == 'event_detected':
                    events_detected += 1
            except Exception:
                continue

    duration_str = '0 hours 0 minutes'
    if first_time and last_time:
        dur = last_time - first_time
        hours, rem = divmod(dur.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        duration_str = f"{int(hours)} hours {int(minutes)} minutes"

    print('\nSESSION SUMMARY')
    print('================')
    print(f'Duration: {duration_str}')
    print(f'Events detected: {events_detected}')
    print(f'Decisions executed: {decisions}')
    print(f'Errors: {errors}\n')
