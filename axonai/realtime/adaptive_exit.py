"""Adaptive Exit Engine.

Replaces fixed ATR TP/SL. Uses the Trade Health Monitor and Market State
to decide when to exit a trade dynamically. "Exit when the original reason disappears."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from axonai.realtime.trade_health_monitor import TradeHealth
from axonai.realtime.regime_engine import RegimeState
from axonai.realtime.liquidity_engine import LiquidityState


@dataclass
class ExitDecision:
    """Output of the exit evaluation."""
    should_exit: bool = False
    action: str = "HOLD"            # "HOLD", "CLOSE_NOW", "ADJUST_SL", "ADJUST_TP"
    reason: str = ""
    suggested_sl: Optional[float] = None
    suggested_tp: Optional[float] = None


class AdaptiveExitManager:
    """Manages active trades, adjusting targets or cutting losses dynamically."""

    def __init__(self, pip_mult: float = 0.0001):
        self._pip = pip_mult
        
        # Current Trade Context
        self._ticket: int = 0
        self._direction: str = ""
        self._entry_price: float = 0.0
        self._current_sl: float = 0.0
        self._current_tp: float = 0.0
        self._initial_sl: float = 0.0
        self._is_breakeven_secured: bool = False

    def register_trade(self, ticket: int, direction: str, entry_price: float, initial_sl: float, initial_tp: float) -> None:
        """Register the trade to track."""
        self._ticket = ticket
        self._direction = direction.upper()
        self._entry_price = entry_price
        self._initial_sl = initial_sl
        self._current_sl = initial_sl
        self._current_tp = initial_tp
        self._is_breakeven_secured = False

    def clear(self) -> None:
        """Clear active tracking."""
        self._ticket = 0
        self._direction = ""

    def evaluate(
        self,
        current_price: float,
        health: TradeHealth,
        regime: RegimeState,
        liquidity: LiquidityState,
    ) -> ExitDecision:
        """Evaluate if we should hold, adjust SL/TP, or force close."""
        if self._ticket == 0:
            return ExitDecision()
            
        pips_profit = (current_price - self._entry_price) / self._pip
        if self._direction == "SELL":
            pips_profit = -pips_profit

        # 1. Critical Health Failure (Time decay or adverse displacement)
        if health.is_failing:
            # Only cut if we are actually underwater or barely profitable.
            # If we are somehow up 20 pips, trailing SL will catch it.
            if pips_profit < 5.0:
                return ExitDecision(
                    should_exit=True,
                    action="CLOSE_NOW",
                    reason=f"Health Failed: {health.reason}"
                )

        # 2. Opposite Liquidity Sweep (The original reason disappeared)
        # If we are short and price sweeps a support level below us, that is a take profit signal.
        if len(liquidity.active_sweeps) > 0:
            for sweep in liquidity.active_sweeps:
                if self._direction == "SELL" and sweep.price < current_price:
                    if pips_profit > 10.0:
                        return ExitDecision(
                            should_exit=True,
                            action="CLOSE_NOW",
                            reason="Take Profit: Support Swept"
                        )
                elif self._direction == "BUY" and sweep.price > current_price:
                    if pips_profit > 10.0:
                        return ExitDecision(
                            should_exit=True,
                            action="CLOSE_NOW",
                            reason="Take Profit: Resistance Swept"
                        )

        # 3. Dynamic Breakeven / Trailing
        # If price reaches +1R (distance from entry to initial SL), secure Breakeven
        sl_distance_pips = abs(self._entry_price - self._initial_sl) / self._pip
        if not self._is_breakeven_secured and sl_distance_pips > 0:
            if pips_profit >= sl_distance_pips:
                self._is_breakeven_secured = True
                
                # Secure BE + 1 pip
                be_price = self._entry_price + (1.0 * self._pip) if self._direction == "BUY" else self._entry_price - (1.0 * self._pip)
                self._current_sl = be_price
                
                return ExitDecision(
                    should_exit=False,
                    action="ADJUST_SL",
                    reason="Secured Breakeven",
                    suggested_sl=be_price
                )

        # 4. Aggressive Trailing in Exhaustion Regime
        # If the market enters EXHAUSTION while we are profitable, tighten SL dramatically
        if regime.regime == "EXHAUSTION" and pips_profit > 10.0:
            trail_dist = 5.0 * self._pip
            new_sl = current_price - trail_dist if self._direction == "BUY" else current_price + trail_dist
            
            # Only move SL forward, never backward
            if (self._direction == "BUY" and new_sl > self._current_sl) or \
               (self._direction == "SELL" and new_sl < self._current_sl):
                self._current_sl = new_sl
                return ExitDecision(
                    should_exit=False,
                    action="ADJUST_SL",
                    reason="Aggressive Trail (Exhaustion Regime)",
                    suggested_sl=new_sl
                )

        return ExitDecision()

__all__ = ["AdaptiveExitManager", "ExitDecision"]
