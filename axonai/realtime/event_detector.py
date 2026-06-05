"""Pure Python event detection engine.

Watches the live state for structural market events and emits
MarketEvent objects. Zero LLM tokens consumed.
"""

from __future__ import annotations

import logging
import queue
from datetime import datetime, timedelta, timezone
from typing import Optional, Set

import numpy as np

from axonai.realtime.event_types import (
    EventPriority, EventType, LiveCandle, MarketEvent
)
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.dataflows.mt5_data import _to_mt5_symbol, get_broker_tz_offset

logger = logging.getLogger(__name__)


class EventDetector:
    """Detects structural market events from live state.

    Detection rules (all pure math, zero LLM cost):

    1. LEVEL_BREACH: Price crosses a key level confirmed by M5 close
    2. STRUCTURE_BREAK: M15 close breaks most recent swing high/low
    3. SWEEP_DETECTED: Price pierces swing level then reverses
    4. VOLATILITY_SPIKE: M5 range > 2x rolling average
    5. CANDLE_PATTERN: Pin bar or engulfing at a key level
    6. REGIME_SHIFT: Dominant regime changes
    7. SESSION_TRANSITION: Session boundary crossed
    8. SPREAD_CHANGE: Spread narrows to safe or widens to danger
    9. MOMENTUM_DIVERGENCE: Price vs RSI divergence
    """

    def __init__(
        self,
        live_state: LiveWorldState,
        live_evidence: LiveMarketEvidence,
        event_queue: queue.Queue,
        config: dict,
    ):
        self.live_state = live_state
        self.live_evidence = live_evidence
        self.event_queue = event_queue
        self.config = config

        # State tracking
        self._consumed_levels: Set[float] = set()
        self._consumed_sweeps: Set[float] = set()
        self._pending_sweeps: list = []  # Sweep confirmation candles
        self._structure_detected_on_candle: bool = True
        self._previous_regime: str = ""
        self._previous_session: str = ""
        self._previous_spread_safe: Optional[bool] = None
        self._cooldown_until: datetime = datetime.min
        self._current_trigger_candle: Optional[LiveCandle] = None

        # Rolling M5 ranges for volatility spike detection
        self._m5_ranges: list = []
        self._m5_range_window: int = 20

        # RSI history for divergence detection
        self._rsi_history: list = []  # [(price, rsi)] tuples
        self._rsi_max_history: int = 10

        # Config
        self._suppress_asian = config.get("realtime_suppress_asian", True)
        self._level_reset_atr_mult = config.get("realtime_level_reset_atr_multiple", 2.0)
        self._log_events = config.get("realtime_log_events", True)
        self._test_mode = config.get("test_mode", False) or not self._log_events
        self._pip_mult = 0.0001  # Updated on init

        # Microstructure Peak & Climax Exhaustion Detector
        from axonai.realtime.peak_detector import PeakDetector
        self.peak_detector = PeakDetector(
            pip_mult=self._pip_mult,
            rule_c_enabled=config.get("peak_detector_rule_c_enabled", False),
        )

    @property
    def tz(self):
        from datetime import timezone, timedelta
        from axonai.dataflows.mt5_data import _to_mt5_symbol, get_broker_tz_offset
        broker_symbol = _to_mt5_symbol(self.live_state.symbol, self.config)
        offset_hours = get_broker_tz_offset(broker_symbol)
        return timezone(timedelta(hours=offset_hours))

    def _enrich_event(self, event: MarketEvent):
        """Add trigger candle details to the event details if available."""
        tc = self._current_trigger_candle
        if tc is None and self.live_evidence._m15_candles:
            tc = self.live_evidence._m15_candles[-1]

        if tc is not None:
            from datetime import timezone
            broker_symbol = _to_mt5_symbol(self.live_state.symbol, self.config)
            offset_hours = get_broker_tz_offset(broker_symbol)
            if isinstance(tc.open_time, datetime):
                aligned_open = tc.open_time - timedelta(hours=offset_hours)
                open_time_utc = int(aligned_open.replace(tzinfo=timezone.utc).timestamp())
            else:
                open_time_utc = tc.open_time
            event.details["trigger_candle"] = {
                "timeframe": tc.timeframe,
                "open": tc.open,
                "high": tc.high,
                "low": tc.low,
                "close": tc.close,
                "open_time": open_time_utc
            }

        # Add mtf_alignment
        me = self.live_evidence.snapshot()
        h4 = getattr(me, "trend_direction_h4", "sideways")
        h1 = getattr(me, "trend_direction_h1", "sideways")
        if h4 == "up" and h1 == "up":
            mtf_align = "BULLISH"
        elif h4 == "down" and h1 == "down":
            mtf_align = "BEARISH"
        else:
            mtf_align = "NEUTRAL"
        event.details["mtf_alignment"] = mtf_align

    def set_pip_multiplier(self, is_jpy: bool):
        """Set pip multiplier based on pair type."""
        self._pip_mult = 0.01 if is_jpy else 0.0001
        self.peak_detector.pip_mult = self._pip_mult

    def on_tick(self, bid: float, ask: float, timestamp: datetime):
        """Lightweight per-tick checks."""
        if not self.live_state.is_initialized:
            return

        mid = (bid + ask) / 2.0
        state = self.live_state._state
        if state is None:
            return

        # 1. Session transition
        session_changed = self.live_state.on_tick(bid, ask, timestamp)
        if session_changed is True and state.session != self._previous_session:
            if self._previous_session:  # Skip first tick
                self._emit(MarketEvent(
                    event_type=EventType.SESSION_TRANSITION,
                    priority=EventPriority.LOW,
                    timestamp=timestamp,
                    symbol=self.live_state.symbol,
                    price=mid,
                    details={
                        "from": self._previous_session,
                        "to": state.session,
                        "penalty": state.session_penalty,
                    }
                ))
            self._previous_session = state.session

        # 2. Spread change
        if self._previous_spread_safe is not None:
            if state.spread_safe != self._previous_spread_safe:
                self._emit(MarketEvent(
                    event_type=EventType.SPREAD_CHANGE,
                    priority=EventPriority.LOW,
                    timestamp=timestamp,
                    symbol=self.live_state.symbol,
                    price=mid,
                    details={
                        "spread_pips": state.spread_pips,
                        "safe": state.spread_safe,
                        "atr_h1": state.atr_14_h1,
                    }
                ))
        self._previous_spread_safe = state.spread_safe

        # 3. Live evidence tick update
        self.live_evidence.on_tick(bid, ask, timestamp)

        # 4. Institutional Level breach check
        self._check_level_breach(bid, ask, timestamp)

        # 5. Microstructure Peak/Valley & Climax exhaustion detection
        self._check_peak_detection(mid, timestamp)

    def _check_peak_detection(self, mid: float, timestamp: datetime):
        """Invoke microstructure peak and climax exhaustion detector."""
        peak_res = self.peak_detector.update(mid, timestamp)
        if peak_res:
            self._emit(MarketEvent(
                event_type=EventType.PEAK_DETECTION,
                priority=EventPriority.HIGH if peak_res.intensity == "HIGH" else EventPriority.MEDIUM,
                timestamp=timestamp,
                symbol=self.live_state.symbol,
                price=mid,
                details=peak_res.to_dict()
            ))

    def on_candle_close(self, candle: LiveCandle):
        """Structural checks on candle close."""
        if not self.live_state.is_initialized:
            return

        state = self.live_state._state
        if state is None:
            return

        self._current_trigger_candle = candle
        self._structure_detected_on_candle = False
        try:
            # Update live state and evidence
            self.live_state.on_candle_close(candle)
            self.live_evidence.on_candle_close(candle)

            if candle.timeframe == "M5":
                self._check_volatility_spike(candle)

            if candle.timeframe in ("M15", "H1", "H4"):
                logger.info("EventDetector: candle close for %s triggering checks...", candle.timeframe)
                self._check_structure_break(candle)
                self._check_sweep(candle)
                self._check_candle_pattern_at_level(candle)

            if candle.timeframe in ("M15", "H1"):
                self._check_regime_shift()
                self._check_momentum_divergence(candle)

            # Reset consumed levels if price has moved far enough
            self._try_reset_consumed_levels(candle.close)
        finally:
            self._current_trigger_candle = None

    def _check_level_breach(self, bid: float, ask: float, timestamp: datetime):
        """Check for breach of active institutional levels on tick.

        Uses LevelBehaviorTracker data to filter false breaches:
        - Levels with sharp rejection velocity → lower breach confidence
        - Levels with high absorption → higher breach confidence
        """
        pip = self._pip_mult
        active_levels = [l for l in self.live_evidence.price_levels if l.is_active and l.strength >= 0.2]
        level_tracker = self.live_evidence._level_tracker

        for level in active_levels:
            distance = abs(bid - level.price)

            # Price is within 2 pips of level
            if distance <= 2 * pip:
                # Only fire if we haven't fired on this level recently
                level_key = round(level.price, 4)
                if level_key in self._consumed_levels:
                    continue

                # Check rejection velocity from level tracker
                bhv = level_tracker.get_level_behavior(level.price)
                rejection_vel = bhv.last_rejection_velocity if bhv else 0.0
                absorbing = level_tracker.is_absorbing(level.price)
                attack_count = level_tracker.get_attack_count(level.price)

                # If level has sharp rejections and no absorption, it's still strong
                if rejection_vel > 8.0 and not absorbing and attack_count < 3:
                    if self._log_events:
                        logger.debug(
                            "Level breach suppressed (sharp rejection): %.5f vel=%.1f",
                            level.price, rejection_vel,
                        )
                    continue

                # 3+ attacks with absorption → upgrade priority
                priority = EventPriority.HIGH if level.strength >= 0.7 else EventPriority.MEDIUM
                if absorbing and attack_count >= 3:
                    priority = EventPriority.HIGH

                self._emit(MarketEvent(
                    event_type=EventType.LEVEL_BREACH,
                    priority=priority,
                    timestamp=timestamp,
                    symbol=self.live_state.symbol,
                    price=bid,
                    details={
                        "level_type": level.level_type,
                        "level_price": level.price,
                        "strength": level.strength,
                        "touches": level.touches,
                        "direction": level.direction,
                        "distance_pips": distance / pip,
                        "attack_count": attack_count,
                        "rejection_velocity": round(rejection_vel, 2),
                        "is_absorbing": absorbing,
                    }
                ))
                self._consumed_levels.add(level_key)


    def _check_structure_break(self, candle: LiveCandle):
        """Detect Break of Structure (BOS)."""
        swing_highs = self.live_evidence.swing_highs
        swing_lows = self.live_evidence.swing_lows

        if swing_highs:
            latest_sh = swing_highs[0]["price"]
            if candle.close > latest_sh and candle.open <= latest_sh:
                self._emit(MarketEvent(
                    event_type=EventType.STRUCTURE_BREAK,
                    priority=EventPriority.HIGH,
                    timestamp=candle.open_time,
                    symbol=self.live_state.symbol,
                    price=candle.close,
                    details={
                        "broken_level": latest_sh,
                        "direction": "bullish_bos",
                        "timeframe": candle.timeframe,
                    }
                ))

        if swing_lows:
            latest_sl = swing_lows[0]["price"]
            if candle.close < latest_sl and candle.open >= latest_sl:
                self._emit(MarketEvent(
                    event_type=EventType.STRUCTURE_BREAK,
                    priority=EventPriority.HIGH,
                    timestamp=candle.open_time,
                    symbol=self.live_state.symbol,
                    price=candle.close,
                    details={
                        "broken_level": latest_sl,
                        "direction": "bearish_bos",
                        "timeframe": candle.timeframe,
                    }
                ))

    def _check_sweep(self, candle: LiveCandle):
        """Detect liquidity sweeps with confirmation candle.

        Phase 1: A candle pierces a swing level and closes back inside →
        stored as pending (NOT emitted).

        Phase 2: On each subsequent candle close, check if pending sweeps
        confirm (next candle closes in reversal direction). Emit only on
        confirmation. Discard after 3 candles without confirmation.
        """
        # --- Phase 2: check existing pending sweeps for confirmation ---
        confirmed = []
        for pending in list(self._pending_sweeps):
            pending["candles_since"] += 1
            if pending["candles_since"] > 3:
                # Expired — discard
                if self._log_events:
                    logger.debug("Sweep pending expired (3 candles): %.5f %s",
                                 pending["swept_level"], pending["direction"])
                self._consumed_sweeps.discard(round(pending["swept_level"], 5))
                self._pending_sweeps.remove(pending)
                continue

            # Check if this is the first confirming candle
            if pending["candles_since"] == 1:
                direction = pending["direction"]
                if direction == "bearish_sweep":
                    # Confirmation: candle closes lower (bearish reversal)
                    if candle.close < candle.open:
                        confirmed.append(pending)
                elif direction == "bullish_sweep":
                    # Confirmation: candle closes higher (bullish reversal)
                    if candle.close > candle.open:
                        confirmed.append(pending)
                # If the candle did NOT confirm, keep waiting (up to 3 total)

        for pend in confirmed:
            self._pending_sweeps.remove(pend)
            # Recalculate absorption details for the confirmed event
            level_tracker = self.live_evidence._level_tracker
            attack_count = level_tracker.get_attack_count(pend["swept_level"])
            consecutive = level_tracker.get_consecutive_attacks(pend["swept_level"])
            absorbing = level_tracker.is_absorbing(pend["swept_level"])
            from datetime import timedelta
            candle_end = candle.open_time + timedelta(minutes=15)  # M15 candle close time
            self._emit(MarketEvent(
                event_type=EventType.SWEEP_DETECTED,
                priority=EventPriority.HIGH,
                timestamp=candle_end,  # actual execution time (candle close)
                symbol=self.live_state.symbol,
                price=candle.close,  # entry price = close of confirmation candle
                details={
                    "swept_level": pend["swept_level"],
                    "direction": pend["direction"],
                    "pierce_pips": pend["pierce_pips"],
                    "timeframe": "M15",
                    "attack_count": attack_count,
                    "consecutive_attacks": consecutive,
                    "is_absorbing": absorbing,
                    "sweep_timestamp": pend["timestamp"],  # preserve original sweep time
                    "sweep_price": pend["price"],  # preserve original sweep price
                    "confirmed_on": candle_end,
                }
            ))
            if self._log_events:
                logger.info("Sweep CONFIRMED: %.5f %s candle_n=%d",
                            pend["swept_level"], pend["direction"], pend["candles_since"])

        # --- Phase 1: detect new sweep candidates (store as pending) ---
        swing_highs = self.live_evidence.swing_highs
        swing_lows = self.live_evidence.swing_lows
        state = self.live_state._state
        atr = state.atr_14_h1 if state else 0.001
        level_tracker = self.live_evidence._level_tracker

        for sh in swing_highs:
            level = sh["price"]
            rounded = round(level, 5)
            if rounded in self._consumed_sweeps:
                continue
            # Wick above level but close below it
            if candle.high > level and candle.close < level:
                pierce_distance = candle.high - level
                if pierce_distance < atr:  # Sweep, not breakout
                    self._consumed_sweeps.add(rounded)
                    self._pending_sweeps.append({
                        "swept_level": level,
                        "direction": "bearish_sweep",
                        "pierce_pips": pierce_distance / self._pip_mult,
                        "timestamp": candle.open_time,
                        "price": candle.close,
                        "candles_since": 0,
                    })
                    if self._log_events:
                        logger.debug("Sweep PENDING (bearish): %.5f on %s", level, candle.open_time)
                    break

        for sl in swing_lows:
            level = sl["price"]
            rounded = round(level, 5)
            if rounded in self._consumed_sweeps:
                continue
            # Wick below level but close above it
            if candle.low < level and candle.close > level:
                pierce_distance = level - candle.low
                if pierce_distance < atr:
                    self._consumed_sweeps.add(rounded)
                    self._pending_sweeps.append({
                        "swept_level": level,
                        "direction": "bullish_sweep",
                        "pierce_pips": pierce_distance / self._pip_mult,
                        "timestamp": candle.open_time,
                        "price": candle.close,
                        "candles_since": 0,
                    })
                    if self._log_events:
                        logger.debug("Sweep PENDING (bullish): %.5f on %s", level, candle.open_time)
                    break

    def _check_volatility_spike(self, candle: LiveCandle):
        """Detect volatility spikes on M5."""
        self._m5_ranges.append(candle.range)
        if len(self._m5_ranges) > self._m5_range_window:
            self._m5_ranges = self._m5_ranges[-self._m5_range_window:]

        if len(self._m5_ranges) < 10:
            return

        avg_range = np.mean(self._m5_ranges[:-1])
        if candle.range > 2.0 * avg_range and avg_range > 0:
            self._emit(MarketEvent(
                event_type=EventType.VOLATILITY_SPIKE,
                priority=EventPriority.HIGH,
                timestamp=candle.open_time,
                symbol=self.live_state.symbol,
                price=candle.close,
                details={
                    "range_pips": candle.range / self._pip_mult,
                    "avg_range_pips": avg_range / self._pip_mult,
                    "ratio": candle.range / (avg_range + 1e-8),
                    "timeframe": "M5",
                }
            ))

    def _check_candle_pattern_at_level(self, candle: LiveCandle):
        """Detect candle patterns (Pin Bars & Engulfing) only when at a key level (confluence filter)."""
        key_levels = self.live_evidence.key_levels
        if not key_levels:
            return

        # Check if candle close is within 5 pips of any key level
        pip_5 = 5 * self._pip_mult
        at_level = False
        nearest_level = 0.0
        for level in key_levels:
            if abs(candle.close - level) <= pip_5:
                at_level = True
                nearest_level = level
                break

        if not at_level:
            return

        c_range = candle.range + 1e-8
        pattern = None

        # 1. Pin Bar check
        if candle.body / c_range < 0.30:
            if candle.upper_shadow / c_range > 0.60 and candle.close <= candle.open:
                pattern = "pin_bar_bearish"
            elif candle.lower_shadow / c_range > 0.60 and candle.close >= candle.open:
                pattern = "pin_bar_bullish"

        # Determine history list based on timeframe
        history_list = []
        if candle.timeframe == "M15":
            history_list = list(self.live_evidence._m15_candles)
        elif candle.timeframe == "H1":
            history_list = list(self.live_evidence._h1_candles)
        elif candle.timeframe == "H4":
            history_list = list(self.live_evidence._h4_candles)

        # 2. Engulfing check (requires previous candle)
        if not pattern and len(history_list) >= 2:
            prev = history_list[-2]
            is_curr_bullish = candle.close > candle.open
            is_prev_bullish = prev.close > prev.open
            
            curr_body = abs(candle.close - candle.open)
            prev_body = abs(prev.close - prev.open)

            if is_curr_bullish and not is_prev_bullish:
                if candle.open <= prev.close and candle.close >= prev.open and curr_body > prev_body:
                    pattern = "engulfing_bullish"
            elif not is_curr_bullish and is_prev_bullish:
                if candle.open >= prev.close and candle.close <= prev.open and curr_body > prev_body:
                    pattern = "engulfing_bearish"

        if pattern:
            # Filter: Bullish patterns must be near support (nearest_level <= candle.close), bearish near resistance (nearest_level >= candle.close)
            if "bullish" in pattern and nearest_level > candle.close:
                return
            if "bearish" in pattern and nearest_level < candle.close:
                return

            # Calculate dynamic level interaction count from history
            interactions = 0
            for hist_c in history_list:
                if hist_c.low <= nearest_level <= hist_c.high:
                    interactions += 1

            # Calculate relative candle momentum (current body size relative to last 10 average)
            avg_body = 0.001
            history_bodies = [abs(c.close - c.open) for c in history_list[-11:-1]] # exclude current
            if history_bodies:
                avg_body = float(np.mean(history_bodies)) + 1e-8
            
            curr_body = abs(candle.close - candle.open)
            momentum_ratio = curr_body / avg_body

            # Determine intensity grade
            if interactions >= 5 or momentum_ratio >= 1.8:
                intensity_grade = "HIGH"
            elif interactions >= 3 or momentum_ratio >= 1.2:
                intensity_grade = "MEDIUM"
            else:
                if self._test_mode:
                    intensity_grade = "LOW"
                else:
                    return  # Skip low-significance pattern noise completely to avoid confusing the user



            self._emit(MarketEvent(
                event_type=EventType.CANDLE_PATTERN,
                priority=EventPriority.HIGH if intensity_grade == "HIGH" else EventPriority.MEDIUM,
                timestamp=candle.open_time,
                symbol=self.live_state.symbol,
                price=candle.close,
                details={
                    "pattern": pattern,
                    "at_level": nearest_level,
                    "timeframe": candle.timeframe,
                    "level_interactions": interactions,
                    "momentum_intensity": round(momentum_ratio, 2),
                    "intensity_grade": intensity_grade,
                }
            ))

    def _check_regime_shift(self):
        """Detect when the dominant regime changes."""
        state = self.live_state._state
        if state is None:
            return

        current_regime = state.dominant_regime
        if self._previous_regime and current_regime != self._previous_regime:
            self._emit(MarketEvent(
                event_type=EventType.REGIME_SHIFT,
                priority=EventPriority.MEDIUM,
                timestamp=datetime.now(),
                symbol=self.live_state.symbol,
                price=0.0,  # Not price-specific
                details={
                    "from": self._previous_regime,
                    "to": current_regime,
                    "confidence": state.regime_confidence,
                    "scores": dict(state.regime_scores),
                }
            ))
        self._previous_regime = current_regime

    def _check_momentum_divergence(self, candle: LiveCandle):
        """Detect price vs RSI divergence."""
        rsi = self.live_state.current_rsi
        self._rsi_history.append((candle.close, rsi))
        if len(self._rsi_history) > self._rsi_max_history:
            self._rsi_history = self._rsi_history[-self._rsi_max_history:]

        if len(self._rsi_history) < 4:
            return

        prices = [p for p, _ in self._rsi_history]
        rsis = [r for _, r in self._rsi_history]

        # Bearish divergence: price higher high, RSI lower high
        if prices[-1] > max(prices[-4:-1]) and rsis[-1] < max(rsis[-4:-1]):
            self._emit(MarketEvent(
                event_type=EventType.MOMENTUM_DIVERGENCE,
                priority=EventPriority.MEDIUM,
                timestamp=candle.open_time,
                symbol=self.live_state.symbol,
                price=candle.close,
                details={
                    "type": "bearish_divergence",
                    "price": candle.close,
                    "rsi": rsi,
                    "timeframe": candle.timeframe,
                }
            ))

        # Bullish divergence: price lower low, RSI higher low
        if prices[-1] < min(prices[-4:-1]) and rsis[-1] > min(rsis[-4:-1]):
            self._emit(MarketEvent(
                event_type=EventType.MOMENTUM_DIVERGENCE,
                priority=EventPriority.MEDIUM,
                timestamp=candle.open_time,
                symbol=self.live_state.symbol,
                price=candle.close,
                details={
                    "type": "bullish_divergence",
                    "price": candle.close,
                    "rsi": rsi,
                    "timeframe": candle.timeframe,
                }
            ))

    def _try_reset_consumed_levels(self, current_price: float):
        """Reset consumed levels when price pulls back significantly."""
        state = self.live_state._state
        if state is None:
            return

        atr = state.atr_14_h1
        reset_dist = atr * self._level_reset_atr_mult

        to_remove = set()
        for level in self._consumed_levels:
            if abs(current_price - level) > reset_dist:
                to_remove.add(level)

        self._consumed_levels -= to_remove
        if to_remove and self._log_events:
            logger.debug("Reset %d consumed levels (price moved away)", len(to_remove))

        to_remove_sweeps = set()
        for level in self._consumed_sweeps:
            if abs(current_price - level) > reset_dist:
                to_remove_sweeps.add(level)

        self._consumed_sweeps -= to_remove_sweeps
        if to_remove_sweeps and self._log_events:
            logger.debug("Reset %d consumed sweeps (price moved away)", len(to_remove_sweeps))

    def _emit(self, event: MarketEvent):
        """Push event into queue with filtering."""
        if getattr(self, "is_in_trade", False):
            if self._log_events:
                logger.debug("Event suppressed (in active trade): %s", event)
            return

        self._enrich_event(event)
        
        # Track if a tradable structure is detected
        if event.event_type in (EventType.STRUCTURE_BREAK, EventType.SWEEP_DETECTED):
            self._structure_detected_on_candle = True

        # Cooldown check
        if datetime.now() < self._cooldown_until:
            if self._log_events:
                logger.debug("Event suppressed (cooldown): %s", event)
            return

        # Hard UTC session block: only allow events between 07:00-20:00 UTC
        evt = event.timestamp
        if evt.tzinfo:
            evt = evt.astimezone(timezone.utc)
        utc_hour = evt.hour + evt.minute / 60.0
        if not (7.0 <= utc_hour < 20.0):
            if self._log_events:
                logger.debug("Event suppressed (outside 07:00-20:00 UTC): %s", event)
            return

        # Asian session filter
        if self._suppress_asian:
            state = self.live_state._state
            if state and state.session == "asian" and event.priority.value < EventPriority.HIGH.value:
                if self._log_events:
                    logger.debug("Event suppressed (asian session): %s", event)
                return

        # Spread gate: suppress all events when spread is dangerously wide
        state = self.live_state._state
        if state and not state.spread_safe and event.priority.value < EventPriority.CRITICAL.value:
            if self._log_events:
                logger.debug("Event suppressed (wide spread): %s", event)
            return

        if self._log_events:
            logger.info("EVENT DETECTED: %s", event)

        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            logger.warning("Event queue full, dropping: %s", event)

    def set_cooldown(self, seconds: float):
        """Set cooldown period after graph execution."""
        self._cooldown_until = datetime.now() + timedelta(seconds=seconds)

    def reset_consumed_level(self, level: float):
        """Manually reset a specific consumed level."""
        self._consumed_levels.discard(round(level, 5))
        self._consumed_sweeps.discard(round(level, 5))

    def backfill_historical_events(self):
        """Run pattern and structural checks on historical candles.
        Populates dashboard history without queuing for execution.
        """
        logger.info("EventDetector: backfilling historical events from seeded candle history...")
        m15_candles = list(self.live_evidence._m15_candles)
        if not m15_candles:
            logger.info("EventDetector: no historical M15 candles found to backfill.")
            return

        from axonai.realtime.api_server import get_dashboard
        dashboard = get_dashboard()

        # Detour the normal _emit to collect historical events instead of placing them in the live queue
        historical_events = []
        original_emit = self._emit

        def mock_emit(event: MarketEvent):
            self._enrich_event(event)
            historical_events.append(event)

        self._emit = mock_emit

        try:
            # Clear self.live_evidence._m15_candles temporarily
            self.live_evidence._m15_candles.clear()
            # Seed the first 20 candles as warm-up only if we have sufficient history
            warmup_count = 20 if len(m15_candles) > 25 else 0
            for i in range(warmup_count):
                self.live_evidence._m15_candles.append(m15_candles[i])
                
            for candle in m15_candles[warmup_count:]:
                self.live_evidence._m15_candles.append(candle)
                self._current_trigger_candle = candle
                self._structure_detected_on_candle = False
                try:
                    self._check_structure_break(candle)
                    self._check_sweep(candle)
                    self._check_candle_pattern_at_level(candle)
                finally:
                    self._current_trigger_candle = None
        except Exception as e:
            logger.error("EventDetector: error during historical backfill: %s", e, exc_info=True)
        finally:
            # Always restore the original emit method
            self._emit = original_emit
            # Restore the full seeded candle list back in self.live_evidence
            self.live_evidence._m15_candles.clear()
            self.live_evidence._m15_candles.extend(m15_candles)

        # Broadcast historical events to the dashboard cache to hydrate the UI immediately
        if dashboard and historical_events:
            # Sort events by timestamp so they appear in correct chronological order
            historical_events.sort(key=lambda x: x.timestamp)
            logger.info("EventDetector: broadcasting %d historical events to dashboard cache", len(historical_events))
            
            # Keep only the last 30 events to match the standard cache size
            for idx, event in enumerate(historical_events[-30:]):
                dashboard.broadcast({
                    "type": "event",
                    "id": f"h-{idx}",
                    "event_type": event.event_type.value,
                    "priority": event.priority.name,
                    "price": event.price,
                    "details": event.details,
                    "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "skipped",
                    "reason": "historical data",
                    "historical": True
                })

