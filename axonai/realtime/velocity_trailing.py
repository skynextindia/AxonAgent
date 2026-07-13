"""
Real-Time Velocity-Based Trailing with Retest Detection.

Trails SL based on:
  1. Velocity gain (acceleration - is price moving faster?)
  2. Price move away from SL (directional confirmation)
  3. Retest detection (price touched SL area, bounced back up = trail it as support)

Uses dynamic market buffer (MarketBufferEngine) instead of fixed thresholds.
Adapts SL trail distance to market regime and volatility.
"""

import logging
from typing import Dict, Optional

from axonai.realtime.market_buffer_engine import MarketBufferEngine
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState
from axonai.realtime.regime_engine import RegimeState

logger = logging.getLogger(__name__)

# Reference volatility length-scale (pips) at which pip-distance constants are
# unscaled. Chosen so EURUSD-normal behavior is IDENTICAL to today: the legacy
# constants were tuned on EURUSD, whose typical 10s absolute-excursion length
# sits around this value. CALIBRATED 2026-07-09 from persisted per-session
# EURUSD vol_pips baselines (asian 0.75, london 2.74, overlap 1.06, ny 0.81,
# rollover 0.67 -> median ~1.0). The old 3.0 guess pinned vol_scale at the 0.4
# floor in 4/5 sessions, shrinking every trail distance to 40% and knocking
# stops out on noise. 1.0 restores ~full intended width and lets London widen.
VOL_PIPS_REF = 1.0
# Scaling clamp: effective constant stays within [0.4x, 2.5x] of its original.
_VOL_SCALE_MIN = 0.4
_VOL_SCALE_MAX = 2.5


class VelocityTrailingManager:
    """
    Real-time velocity trailing with dynamic market buffer adaptation.

    Key insight: SL trails when price accelerates AWAY from it + retests show it's support.
    Trail distance adapts to market regime (tight in compression, loose in expansion).
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self._trail_state: Dict[int, dict] = {}
        self._buffer_engine = MarketBufferEngine(config=config)

        # Velocity tracking
        self.velocity_acceleration_threshold = 1.2  # 20% velocity increase = acceleration

        # Retest detection
        self.retest_window_pips = 3.0  # How close to SL counts as "touching" support
        self.retest_bounce_pips = 1.0   # Minimum bounce off support to count as retest

        # Trailing parameters (these now have dynamic alternatives)
        self.min_price_distance_to_trail = float(self.config.get("realtime_min_price_distance_to_trail", 2.0))
        self.max_trail_distance = float(self.config.get("realtime_max_trail_distance", 15.0))
        self.base_trail_buffer = float(self.config.get("realtime_base_trail_buffer", 7.5))
        self.min_trail_floor_pips = float(self.config.get("realtime_min_trail_floor_pips", 4.0))

        # MTF retrace delay parameters
        self.enable_mtf_retrace_delay = bool(self.config.get("enable_mtf_retrace_delay", True))
        self.mtf_retrace_threshold_pips = float(self.config.get("mtf_retrace_threshold_pips", 1.0))

    def _vol_scale(self, velocity: Optional[NormalizedVelocity]) -> float:
        """Reference-ratio scale for pip-distance constants.

        effective = original * (vol_pips / VOL_PIPS_REF), clamped to
        [_VOL_SCALE_MIN, _VOL_SCALE_MAX]. Returns 1.0 (identity, behavior
        unchanged) when vol_pips is unavailable or == VOL_PIPS_REF.
        """
        if velocity is None:
            return 1.0
        vp = getattr(velocity, "vol_pips", None)
        if vp is None or vp <= 0.0:
            return 1.0
        ref = float(self.config.get("vol_pips_ref", VOL_PIPS_REF)) or VOL_PIPS_REF
        ratio = vp / ref
        return max(_VOL_SCALE_MIN, min(ratio, _VOL_SCALE_MAX))

    def on_tick(
        self,
        ticket: int,
        bid: float,
        ask: float,
        position_type: str,
        entry_price: float,
        initial_sl: float,
        current_sl: float,
        velocity_percentile: float,
        velocity_acceleration: float,
        displacement_ratio: float,
        health_score: float,
        at_structure: bool,
        lowest_price: float,
        velocity: Optional[NormalizedVelocity] = None,
        displacement: Optional[DisplacementState] = None,
        regime: Optional[RegimeState] = None,
        ticks_in_trade: int = 0,
        is_htf_aligned: bool = False,
        pip: float = 0.0001,
        symbol: str = "EURUSD",
        h1_atr: float = 0.0,
    ) -> Optional[dict]:
        """
        Real-time velocity trailing with retest detection and dynamic market buffer.

        Args:
            ticket: Trade ticket number
            bid/ask: Current bid/ask prices
            position_type: "BUY" or "SELL"
            entry_price: Entry price
            initial_sl/current_sl: Stop loss prices
            velocity_percentile: Velocity percentile (0-100)
            velocity_acceleration: Velocity change rate (1.0 = no change, 1.2 = 20% faster)
            displacement_ratio: Displacement ratio for the move
            health_score: Trade health score (0-100)
            at_structure: True if at support/resistance
            lowest_price: Lowest price since entry (for retest detection)
            velocity: Optional NormalizedVelocity object (for dynamic buffer)
            displacement: Optional DisplacementState object (for dynamic buffer)
            regime: Optional RegimeState object (for dynamic buffer)
            ticks_in_trade: How many ticks in this trade
            is_htf_aligned: True if higher timeframe aligned with trade
            pip: The pip size multiplier of the symbol (e.g. 0.0001 or 0.01)

        Returns:
            dict with new_sl if trail triggered, else None
        """

        # Initialize state
        if ticket not in self._trail_state:
            self._trail_state[ticket] = {
                "last_velocity_percentile": velocity_percentile,
                "last_trail_price": entry_price,
                "retest_count": 0,
                "support_level": None,
                "dynamic_buffer": None,
                "smoothed_width_mult": None,
                "peak_price": bid if position_type == "BUY" else ask,
            }

        state = self._trail_state[ticket]

        # Update peak price (most favorable price seen since entry)
        if position_type == "BUY":
            if "peak_price" not in state or bid > state["peak_price"]:
                state["peak_price"] = bid
        else:
            if "peak_price" not in state or ask < state["peak_price"]:
                state["peak_price"] = ask

        # Single volatility-length-scale multiplier reused for all pip-distance
        # constants this tick. 1.0 (identity) when vol_pips unavailable/==REF.
        vol_scale = self._vol_scale(velocity)

        # Compute dynamic market buffer (replaces static thresholds)
        if velocity and displacement and regime:
            dyn_buffer = self._buffer_engine.compute(
                regime=regime,
                velocity=velocity,
                displacement=displacement,
                ticks_in_trade=ticks_in_trade,
                is_htf_aligned=is_htf_aligned,
            )
            state["dynamic_buffer"] = dyn_buffer
        else:
            dyn_buffer = None

        # Current profit
        if position_type == "BUY":
            current_profit = (bid - entry_price) / pip
            distance_from_sl = (bid - current_sl) / pip
        else:
            current_profit = (entry_price - ask) / pip
            distance_from_sl = (current_sl - ask) / pip

        # Only trail if profitable (any profit > 0)
        if current_profit <= 0:
            return None

        # Trailing Stop Activation Gate
        # Prevent trailing too early by requiring a minimum profit threshold proportional to the pair's scale.
        _scale = float(self.config.get("pair_move_scale", 1.0))
        activation_pips = float(self.config.get("realtime_trail_activation_pips", 5.0)) * _scale
        if current_profit < activation_pips:
            return None

        # MTF Retrace Delay Check: prevent trailing stop cuts on pullbacks when HTF is aligned
        enable_retrace_delay = self.config.get("enable_mtf_retrace_delay", True)
        
        # Scale the retrace threshold dynamically by the pair's volatility scale and the live session noise
        session_vol = 1.0
        if velocity and hasattr(velocity, "vol_pips") and velocity.vol_pips is not None:
            session_vol = max(0.5, velocity.vol_pips)  # Floor at 0.5 to prevent dividing by zero
            
        retrace_threshold = float(self.config.get("mtf_retrace_threshold_pips", 1.5)) * _scale * session_vol
        
        if is_htf_aligned and enable_retrace_delay:
            if position_type == "BUY":
                retrace_pips = (state["peak_price"] - bid) / pip
            else:
                retrace_pips = (ask - state["peak_price"]) / pip
                
            if retrace_pips > retrace_threshold:
                logger.info(
                    "VelocityTrail #%d: Retrace delay active. Price retraced %.1f pips from peak %.5f. MTF aligned, skipping SL update.",
                    ticket, retrace_pips, state["peak_price"]
                )
                return None

        # Detect velocity acceleration (getting faster)
        velocity_accelerating = velocity_acceleration >= self.velocity_acceleration_threshold

        # Detect retest: price came back and touched SL area, now bouncing up
        retest_detected = self._detect_retest(
            ticket, position_type, bid, ask, current_sl, lowest_price, pip,
            retest_window_pips=self.retest_window_pips * vol_scale,
        )

        # Scale pip thresholds dynamically for JPY, GBP and Gold to prevent stop-choking
        scale = 1.0
        is_gold = (pip == 0.01 and entry_price > 1000.0)
        if pip == 0.01:
            if entry_price > 1000.0:
                scale = 15.0  # Gold (e.g. 15x scaling on EURUSD values)
            else:
                scale = 3.0   # JPY
        elif "GBP" in symbol.upper():
            scale = 1.6       # GBP pairs (higher volatility)

        # Trail conditions:
        # 1. Velocity is accelerating (price moving faster = momentum building)
        # 2. Retest detected (price tested support, confirmed it holds)
        # 3. Price moving away from SL (directional confirmation)
        # 4. Health is good (thesis still valid)

        should_trail = (
            (velocity_accelerating or retest_detected) and
            distance_from_sl >= self.min_price_distance_to_trail * scale * vol_scale and
            health_score >= 50.0
        )

        if not should_trail:
            return None

        # Calculate trail aggressiveness based on real market conditions
        agg = self._calculate_dynamic_aggressiveness(
            velocity_percentile,
            velocity_acceleration,
            displacement_ratio,
            health_score,
            retest_detected
        )

        # Momentum-aware trail distance: wide while the move is intact and in our
        # favor, tight as momentum exhausts. Replaces the proximity-driven collapse.
        raw_width_mult = self._momentum_width_mult(velocity, position_type)
        
        # Smooth the multiplier to prevent instant collapse on a single low-velocity tick.
        # Gold uses a much slower EMA (alpha=0.02, ~50-tick window) to absorb momentary
        # tick pauses that would otherwise collapse width_mult to 0.4 and choke the stop.
        if state.get("smoothed_width_mult") is None:
            state["smoothed_width_mult"] = raw_width_mult
        else:
            alpha = 0.02 if is_gold else 0.1  # Gold: ~50-tick window; FX: ~10-tick
            state["smoothed_width_mult"] = (alpha * raw_width_mult) + ((1.0 - alpha) * state["smoothed_width_mult"])

        width_mult = state["smoothed_width_mult"]
        # Gold: enforce a minimum width_mult so a single quiet tick cannot collapse
        # the stop (decay=0 → raw=0.4 was the choking culprit).
        if is_gold:
            width_mult = max(width_mult, 1.2)
            state["smoothed_width_mult"] = max(state["smoothed_width_mult"], 1.2)
        trail_distance = self._calculate_trail_distance(
            current_profit, width_mult, vol_scale, scale, h1_atr=h1_atr, pip=pip, is_gold=is_gold
        )

        # Include dynamic buffer info in log
        buffer_info = f" [buffer={dyn_buffer.threshold:.3f} regime={dyn_buffer.regime_name}]" if dyn_buffer else ""

        if position_type == "BUY":
            new_sl = bid - (trail_distance * pip)
            if new_sl > current_sl:  # Only move SL up
                state["last_trail_price"] = bid
                reason_str = f"Velocity trail (agg={agg:.2f}, accel={velocity_acceleration:.2f}, retest={retest_detected}, regime={dyn_buffer.regime_name if dyn_buffer else 'static'})"
                if "trail_history" not in state:
                    state["trail_history"] = []
                from datetime import datetime
                state["trail_history"].append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "price": bid,
                    "new_sl": new_sl,
                    "distance": round(trail_distance, 1),
                    "reason": reason_str
                })
                logger.info(
                    "VelocityTrail BUY #%d: SL %.5f -> %.5f (agg=%.2f, vel_accel=%.2f, retest=%s)%s",
                    ticket, current_sl, new_sl, agg, velocity_acceleration, retest_detected, buffer_info
                )
                return {
                    "new_sl": new_sl,
                    "reason": reason_str,
                    "aggressiveness": agg,
                    "profit_locked": (new_sl - entry_price) / pip,
                    "dynamic_buffer": dyn_buffer.threshold if dyn_buffer else None,
                }
        else:
            new_sl = ask + (trail_distance * pip)
            if new_sl < current_sl or current_sl == 0.0:  # Only move SL down
                state["last_trail_price"] = ask
                reason_str = f"Velocity trail (agg={agg:.2f}, accel={velocity_acceleration:.2f}, retest={retest_detected}, regime={dyn_buffer.regime_name if dyn_buffer else 'static'})"
                if "trail_history" not in state:
                    state["trail_history"] = []
                from datetime import datetime
                state["trail_history"].append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "price": ask,
                    "new_sl": new_sl,
                    "distance": round(trail_distance, 1),
                    "reason": reason_str
                })
                logger.info(
                    "VelocityTrail SELL #%d: SL %.5f -> %.5f (agg=%.2f, vel_accel=%.2f, retest=%s)%s",
                    ticket, current_sl, new_sl, agg, velocity_acceleration, retest_detected, buffer_info
                )
                return {
                    "new_sl": new_sl,
                    "reason": reason_str,
                    "aggressiveness": agg,
                    "profit_locked": (entry_price - new_sl) / pip,
                    "dynamic_buffer": dyn_buffer.threshold if dyn_buffer else None,
                }

        return None

    def _detect_retest(
        self, ticket: int, position_type: str, bid: float, ask: float,
        current_sl: float, lowest_price: float, pip: float,
        retest_window_pips: Optional[float] = None,
    ) -> bool:
        """Detect if price tested SL area (within window pips) and bounced back up."""

        state = self._trail_state[ticket]
        if retest_window_pips is None:
            retest_window_pips = self.retest_window_pips

        if position_type == "BUY":
            distance_to_sl = (bid - current_sl) / pip
            # Retest: price got within window pips of SL and bounced back
            retest = distance_to_sl <= retest_window_pips
        else:
            distance_to_sl = (current_sl - ask) / pip
            retest = distance_to_sl <= retest_window_pips

        if retest:
            state["retest_count"] += 1
            logger.debug("Retest detected for ticket %d (count: %d)", ticket, state["retest_count"])
            return True

        return False

    def _calculate_dynamic_aggressiveness(
        self, velocity_percentile: float, velocity_acceleration: float,
        displacement_ratio: float, health_score: float, retest_detected: bool
    ) -> float:
        """Calculate aggressiveness based on LIVE conditions, not fixed thresholds."""

        # Velocity component: higher percentile = more aggressive
        vel_score = min(velocity_percentile / 100.0, 1.0)

        # Acceleration component: faster acceleration = more aggressive (multiply by acceleration ratio)
        accel_boost = min(velocity_acceleration / 1.5, 2.0)  # Cap at 2x boost

        # Displacement: trending hard = more aggressive
        disp_score = min(displacement_ratio / 0.5, 1.0)

        # Health: good thesis = more confident trailing
        health_comp = health_score / 100.0

        # Retest confirms support: boost aggressiveness
        retest_boost = 1.3 if retest_detected else 1.0

        # Combine all factors
        agg = (vel_score * 0.35 + disp_score * 0.35 + health_comp * 0.3) * accel_boost * retest_boost

        return min(agg, 1.0)

    def _momentum_width_mult(
        self, velocity: Optional[NormalizedVelocity], position_type: str
    ) -> float:
        """Momentum-state multiplier in [0.4, 2.5].

        High (give the move room) when momentum is intact and pushing the trade
        forward: decay_ratio near peak, in-favor z_score high, clean ticks.
        Low (lock gains) as momentum exhausts (decay_ratio falls, z reverts).
        """
        if velocity is None:
            return 1.0

        decay = getattr(velocity, "decay_ratio", 1.0)
        decay = 1.0 if decay is None else decay
        eff = getattr(velocity, "tick_efficiency", 0.5)
        eff = 0.5 if eff is None else eff

        # Only momentum that pushes the trade forward should widen the trail.
        disp_v = getattr(velocity, "displacement_velocity", 0.0) or 0.0
        in_favor = disp_v > 0 if position_type == "BUY" else disp_v < 0
        z = getattr(velocity, "z_score", 0.0) or 0.0
        z_term = min(max(z, 0.0) / 2.0, 1.0) if in_favor else 0.0

        mult = 0.5 + 1.5 * decay + 0.4 * z_term - 0.5 * (1.0 - eff)
        return max(0.4, min(mult, 2.5))

    def _calculate_trail_distance(
        self,
        current_profit: float,
        width_mult: float,
        vol_scale: float = 1.0,
        scale: float = 1.0,
        h1_atr: float = 0.0,
        pip: float = 0.0001,
        is_gold: bool = False,
    ) -> float:
        """Pips to keep behind price, driven by momentum state.

        width_mult (from momentum) sets the base width; profit allows a mild
        extra tighten. Floored at min_trail_floor_pips so proximity to SL can
        never collapse the stop (the old retest death-spiral).

        Gold ATR floor: for XAUUSD the trailing distance can never be less than
        1.0 × H1 ATR in pips, preventing a $6 stop on a $2000+ instrument.
        """
        profit_factor = min(current_profit / (20.0 * scale), 1.0)  # scale profit lock targets too
        trail_distance = self.base_trail_buffer * scale * vol_scale * width_mult * (1.0 - profit_factor * 0.2)
        trail_distance = max(trail_distance, self.min_trail_floor_pips * scale)
        trail_distance = min(trail_distance, self.max_trail_distance * scale * vol_scale)

        # Gold ATR hard floor: 1.0 × H1 ATR converted to pips (never trail tighter than this)
        if is_gold and h1_atr > 0.0 and pip > 0.0:
            atr_floor_pips = h1_atr / pip
            trail_distance = max(trail_distance, atr_floor_pips)

        return trail_distance

    def reset(self, ticket: Optional[int] = None):
        """Clear trail state when position closes."""
        if ticket:
            self._trail_state.pop(ticket, None)
        else:
            self._trail_state.clear()
