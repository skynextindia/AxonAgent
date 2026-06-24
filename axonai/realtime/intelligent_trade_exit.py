"""Intelligent trade exit decisions based on velocity health.

Exit signals:
- HOLD: Keep trading
- TIGHT_TRAIL: Reduce SL as health deteriorates
- CLOSE_ON_REVERSAL: Market reversal detected
- CLOSE_ON_HEALTH: Trade health collapsed
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from dataclasses import dataclass

from axonai.realtime.trade_velocity_health import TradeVelocityHealth


class ExitSignal(str, Enum):
    """Exit decision signals."""
    HOLD = "HOLD"
    TIGHT_TRAIL = "TIGHT_TRAIL"
    CLOSE_ON_REVERSAL = "CLOSE_ON_REVERSAL"
    CLOSE_ON_HEALTH = "CLOSE_ON_HEALTH"


@dataclass
class ExitDecision:
    """Exit decision with metadata."""
    signal: ExitSignal = ExitSignal.HOLD
    reason: str = ""
    suggested_sl: Optional[float] = None
    trail_pips: Optional[float] = None
    should_close: bool = False


class IntelligentTradeExitManager:
    """Exit decisions based on velocity health + reversal factors."""

    def __init__(self, pip_mult: float = 0.0001):
        self._pip = pip_mult

        # Configurable thresholds
        self.exit_health_threshold = 0.40      # Close if health < this
        self.trail_health_threshold = 0.70     # Tighten trail if health < this
        self.reversal_risk_exit = 0.70         # Close if risk > this
        self.min_profit_threshold = 0.25       # Min profit (as × ATR) before tight trailing

    def decide_exit(
        self,
        velocity_health: TradeVelocityHealth,
        current_price: float,
        entry_price: float,
        direction: str,  # "BUY" or "SELL"
        pips_profit: float,
        atr_pips: float,
    ) -> ExitDecision:
        """
        Decide exit action based on velocity health.

        Returns: ExitDecision with signal, reason, suggested_sl
        """

        # PRIORITY 1: Close on critical reversal risk
        if velocity_health.reversal_risk > self.reversal_risk_exit:
            return ExitDecision(
                signal=ExitSignal.CLOSE_ON_REVERSAL,
                reason=velocity_health.reason,
                should_close=True
            )

        # PRIORITY 2: Close on health score collapse
        if velocity_health.health_score < self.exit_health_threshold:
            return ExitDecision(
                signal=ExitSignal.CLOSE_ON_HEALTH,
                reason=velocity_health.reason,
                should_close=True
            )

        # PRIORITY 3: Tighten trail as health deteriorates
        if velocity_health.health_score < self.trail_health_threshold:
            if pips_profit >= self.min_profit_threshold * atr_pips:
                # Trail distance = (1 - health) * some multiple
                trail_tightness = (1.0 - velocity_health.health_score) / (1.0 - self.trail_health_threshold)
                trail_pips = 0.3 + (trail_tightness * 0.7)  # 0.3-1.0× ATR

                trail_distance = trail_pips * atr_pips * self._pip

                if direction == "BUY":
                    suggested_sl = current_price - trail_distance
                else:
                    suggested_sl = current_price + trail_distance

                return ExitDecision(
                    signal=ExitSignal.TIGHT_TRAIL,
                    reason=f"Health degrading: {velocity_health.reason}",
                    suggested_sl=suggested_sl,
                    trail_pips=trail_pips
                )

        # PRIORITY 4: Hold
        return ExitDecision(
            signal=ExitSignal.HOLD,
            reason=f"Healthy: {velocity_health.reason}"
        )


__all__ = ["IntelligentTradeExitManager", "ExitSignal", "ExitDecision"]
