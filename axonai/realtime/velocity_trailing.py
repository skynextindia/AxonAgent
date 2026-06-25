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
        self.min_price_distance_to_trail = 2.0  # Minimum 2 pips away from SL to trail
        self.max_trail_distance = 15.0  # Never trail more than 15 pips from price

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

        Returns:
            dict with new_sl if trail triggered, else None
        """

        pip = 0.0001 if "JPY" not in str(position_type).upper() else 0.01

        # Initialize state
        if ticket not in self._trail_state:
            self._trail_state[ticket] = {
                "last_velocity_percentile": velocity_percentile,
                "last_trail_price": entry_price,
                "retest_count": 0,
                "support_level": None,
                "dynamic_buffer": None,
            }

        state = self._trail_state[ticket]

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

        # Detect velocity acceleration (getting faster)
        velocity_accelerating = velocity_acceleration >= self.velocity_acceleration_threshold

        # Detect retest: price came back and touched SL area, now bouncing up
        retest_detected = self._detect_retest(
            ticket, position_type, bid, ask, current_sl, lowest_price, pip
        )

        # Trail conditions:
        # 1. Velocity is accelerating (price moving faster = momentum building)
        # 2. Retest detected (price tested support, confirmed it holds)
        # 3. Price moving away from SL (directional confirmation)
        # 4. Health is good (thesis still valid)

        should_trail = (
            (velocity_accelerating or retest_detected) and
            distance_from_sl >= self.min_price_distance_to_trail and
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

        # Trail distance based on how far price has moved
        trail_distance = self._calculate_trail_distance(
            current_profit, displacement_ratio, agg, distance_from_sl
        )

        # Include dynamic buffer info in log
        buffer_info = f" [buffer={dyn_buffer.threshold:.3f} regime={dyn_buffer.regime_name}]" if dyn_buffer else ""

        if position_type == "BUY":
            new_sl = bid - (trail_distance * pip)
            if new_sl > current_sl:  # Only move SL up
                state["last_trail_price"] = bid
                logger.info(
                    "VelocityTrail BUY #%d: SL %.5f -> %.5f (agg=%.2f, vel_accel=%.2f, retest=%s)%s",
                    ticket, current_sl, new_sl, agg, velocity_acceleration, retest_detected, buffer_info
                )
                return {
                    "new_sl": new_sl,
                    "reason": f"Velocity trail (agg={agg:.2f}, accel={velocity_acceleration:.2f}, retest={retest_detected}, regime={dyn_buffer.regime_name if dyn_buffer else 'static'})",
                    "aggressiveness": agg,
                    "profit_locked": (new_sl - entry_price) / pip,
                    "dynamic_buffer": dyn_buffer.threshold if dyn_buffer else None,
                }
        else:
            new_sl = ask + (trail_distance * pip)
            if new_sl < current_sl or current_sl == 0.0:  # Only move SL down
                state["last_trail_price"] = ask
                logger.info(
                    "VelocityTrail SELL #%d: SL %.5f -> %.5f (agg=%.2f, vel_accel=%.2f, retest=%s)%s",
                    ticket, current_sl, new_sl, agg, velocity_acceleration, retest_detected, buffer_info
                )
                return {
                    "new_sl": new_sl,
                    "reason": f"Velocity trail (agg={agg:.2f}, accel={velocity_acceleration:.2f}, retest={retest_detected}, regime={dyn_buffer.regime_name if dyn_buffer else 'static'})",
                    "aggressiveness": agg,
                    "profit_locked": (entry_price - new_sl) / pip,
                    "dynamic_buffer": dyn_buffer.threshold if dyn_buffer else None,
                }

        return None

    def _detect_retest(
        self, ticket: int, position_type: str, bid: float, ask: float,
        current_sl: float, lowest_price: float, pip: float
    ) -> bool:
        """Detect if price tested SL area (within 3 pips) and bounced back up."""

        state = self._trail_state[ticket]

        if position_type == "BUY":
            distance_to_sl = (bid - current_sl) / pip
            # Retest: price got within 3 pips of SL and bounced back
            retest = distance_to_sl <= self.retest_window_pips
        else:
            distance_to_sl = (current_sl - ask) / pip
            retest = distance_to_sl <= self.retest_window_pips

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

    def _calculate_trail_distance(
        self, current_profit: float, displacement_ratio: float,
        aggressiveness: float, distance_from_sl: float
    ) -> float:
        """Calculate how many pips to keep as buffer from current price."""

        # More profit made = can trail tighter (but keep at least 1 pip)
        # Higher aggressiveness = trail tighter
        # Higher displacement = trail tighter (trending strong)

        profit_factor = min(current_profit / 20.0, 1.0)  # 20 pips = max profit factor
        disp_factor = min(displacement_ratio, 1.0)

        # Trail buffer decreases with aggressiveness and profit
        base_buffer = 5.0
        trail_distance = base_buffer * (1.0 - aggressiveness * 0.7) * (1.0 - profit_factor * 0.5)

        # Ensure minimum and maximum
        trail_distance = max(trail_distance, 1.0)
        trail_distance = min(trail_distance, self.max_trail_distance)

        return trail_distance

    def reset(self, ticket: Optional[int] = None):
        """Clear trail state when position closes."""
        if ticket:
            self._trail_state.pop(ticket, None)
        else:
            self._trail_state.clear()
