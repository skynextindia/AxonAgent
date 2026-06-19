"""Trade Health Monitor.

Continuously evaluates the "health" of an open position using displacement,
velocity, and regime context. Determines if the trade hypothesis is still
valid or if the market conditions have fundamentally changed against us.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState, DISPLACEMENT_IMPULSE
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState


@dataclass
class TradeHealth:
    """Output snapshot of trade health."""
    score: float = 1.0                # 0.0 (dead) to 1.0 (perfect)
    is_failing: bool = False          # True if score < 0.2
    time_in_drawdown_sec: float = 0.0 # Time spent underwater
    max_favorable_excursion: float = 0.0
    reason: str = "Healthy"


class TradeHealthMonitor:
    """Evaluates position health relative to its intended market state."""

    def __init__(self, pip_mult: float = 0.0001):
        self._pip = pip_mult
        
        # Position state
        self._is_active = False
        self._ticket: int = 0
        self._direction: str = ""
        self._entry_price: float = 0.0
        self._entry_time: float = 0.0
        
        # Metrics
        self._time_in_drawdown_sec = 0.0
        self._max_favorable_excursion = 0.0
        self._last_tick_time = 0.0
        
        # Expected conditions (recorded at entry)
        self._entry_regime = ""
        self._entry_mtf_bias = 0.0

    def register_trade(self, ticket: int, direction: str, price: float, ts: float, regime: str, mtf_bias: float) -> None:
        """Register a new open position."""
        self._is_active = True
        self._ticket = ticket
        self._direction = direction.upper()
        self._entry_price = price
        self._entry_time = ts
        
        self._time_in_drawdown_sec = 0.0
        self._max_favorable_excursion = 0.0
        self._last_tick_time = ts
        
        self._entry_regime = regime
        self._entry_mtf_bias = mtf_bias

    def clear(self) -> None:
        """Clear active trade state."""
        self._is_active = False

    def evaluate(
        self,
        current_price: float,
        ts: float,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        regime: RegimeState,
        mtf: MTFState,
    ) -> TradeHealth:
        """Evaluate trade health based on current conditions."""
        if not self._is_active:
            return TradeHealth()
            
        dt = ts - self._last_tick_time
        self._last_tick_time = ts
        
        # Calculate PnL in pips
        pips_profit = (current_price - self._entry_price) / self._pip
        if self._direction == "SELL":
            pips_profit = -pips_profit
            
        # Update excursions
        if pips_profit < 0:
            self._time_in_drawdown_sec += dt
        if pips_profit > self._max_favorable_excursion:
            self._max_favorable_excursion = pips_profit
            
        # Time in trade
        trade_duration = ts - self._entry_time
            
        # Baseline score starts at 1.0 and is reduced by warning signs
        score = 1.0
        reason = "Healthy"
        
        # 1. Stagnation / Time decay
        if trade_duration > 3600: # 1 hour
            # If we've been in a trade for an hour and barely moved, thesis is weak
            if self._max_favorable_excursion < 5.0 and pips_profit < 2.0:
                score -= 0.4
                reason = "Stagnant (Time Decay)"
                
        # 2. Drawdown duration
        if self._time_in_drawdown_sec > 1800: # 30 mins underwater
            score -= 0.3
            reason = "Extended Drawdown"
            
        # 3. Adverse Displacement
        # If the market is printing strong impulses AGAINST us
        if displacement.classification == DISPLACEMENT_IMPULSE:
            if self._direction == "BUY" and displacement.sell_displacement > displacement.buy_displacement * 2:
                score -= 0.5
                reason = "Adverse Impulse (Selling)"
            elif self._direction == "SELL" and displacement.buy_displacement > displacement.sell_displacement * 2:
                score -= 0.5
                reason = "Adverse Impulse (Buying)"
                
        # 4. Failed Breakout / Fakeout Return
        # If we had a nice run (+10 pips) and it fully retraced to negative
        if self._max_favorable_excursion > 10.0 and pips_profit < -2.0:
            score -= 0.6
            reason = "Failed Expansion (Full Retracement)"
            
        # 5. Regime Shift
        # E.g. we entered a TREND, but it shifted to CHOP or REVERSAL against us
        if self._entry_regime != regime.regime and trade_duration > 600:
            # Reversal against us is bad
            if regime.regime == "REVERSAL":
                if (self._direction == "BUY" and regime.trend_direction == "down") or \
                   (self._direction == "SELL" and regime.trend_direction == "up"):
                    score -= 0.4
                    reason = "Adverse Regime Shift (Reversal)"
                    
        score = max(0.0, score)
        
        return TradeHealth(
            score=round(score, 2),
            is_failing=score < 0.2,
            time_in_drawdown_sec=self._time_in_drawdown_sec,
            max_favorable_excursion=round(self._max_favorable_excursion, 1),
            reason=reason if score < 1.0 else "Healthy"
        )

__all__ = ["TradeHealthMonitor", "TradeHealth"]
