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
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import (
    DisplacementState,
    DISPLACEMENT_IMPULSE,
    DISPLACEMENT_EXHAUSTION,
    DISPLACEMENT_TRAP,
    DISPLACEMENT_ABSORPTION,
    DISPLACEMENT_NEUTRAL,
)
from axonai.realtime.trade_phase import TradePhase
from axonai.realtime.exit_stats import ExitStats
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.trade_velocity_health import TradeVelocityHealth
from axonai.realtime.intelligent_trade_exit import IntelligentTradeExitManager


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

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        self._pip = pip_mult
        self.config = config or {}

        # Intelligent exit manager (velocity-based)
        self.intelligent_exit = IntelligentTradeExitManager(pip_mult=pip_mult)

        # Current Trade Context
        self._ticket: int = 0
        self._direction: str = ""
        self._entry_price: float = 0.0
        self._current_sl: float = 0.0
        self._current_tp: float = 0.0
        self._initial_sl: float = 0.0
        self._is_breakeven_secured: bool = False

    def register_trade(self, ticket: int, direction: str, entry_price: float, initial_sl: float, initial_tp: float, is_sweep: bool = False) -> None:
        """Register the trade to track."""
        self._ticket = ticket
        self._direction = direction.upper()
        self._entry_price = entry_price
        self._initial_sl = initial_sl
        self._current_sl = initial_sl
        self._current_tp = initial_tp
        self._is_breakeven_secured = False
        self._is_sweep = is_sweep

    def clear(self) -> None:
        """Clear active tracking."""
        self._ticket = 0
        self._direction = ""

    def _close(self, reason: str) -> ExitDecision:
        return ExitDecision(should_exit=True, action="CLOSE_NOW", reason=reason)

    def _tight_trail(self, price: float, pips: float) -> float:
        trail_dist = pips * self._pip
        if self._direction == "BUY":
            return price - trail_dist
        else:
            return price + trail_dist

    def _market_energy(
        self,
        vel: NormalizedVelocity,
        disp: DisplacementState,
    ) -> str:
        cls = disp.classification

        if cls == DISPLACEMENT_IMPULSE:
            favour = (
                (self._direction == "BUY"  and disp.net_displacement_pips > 0) or
                (self._direction == "SELL" and disp.net_displacement_pips < 0)
            )
            return "HEALTHY_IMPULSE" if favour else "ADVERSE_IMPULSE"

        if cls == DISPLACEMENT_EXHAUSTION:
            return "EXHAUSTING"

        # TRAP and ABSORPTION: high velocity but no net move — liquidity fight, not directional
        if cls in (DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION):
            return "NOISE"

        return "NOISE"

    def _check_exhaustion_tp(
        self,
        price: float,
        vel: NormalizedVelocity,
        disp: DisplacementState,
        liq: LiquidityState,
        pips_profit: float,
        phase: TradePhase,
        atr_pips: float,
    ) -> Optional[ExitDecision]:
        if pips_profit < 0.4 * atr_pips:
            return None

        is_exhaustion_energy = (
            vel.is_unusual and vel.tick_efficiency < 0.2 and
            disp.classification in (
                DISPLACEMENT_EXHAUSTION, DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION
            )
        )
        if not is_exhaustion_energy:
            return None

        # Check if a level in our profit direction has been swept
        target_swept = any(
            (self._direction == "SELL" and s.price < price) or
            (self._direction == "BUY"  and s.price > price)
            for s in liq.active_sweeps
        )

        if target_swept:
            return self._close("Exhaustion TP: Target level swept")

        if liq.distance_to_nearest_level < 0.65 * atr_pips:
            # Near structure but not swept → tighten, do not close
            new_sl = self._tight_trail(price, pips=0.25 * atr_pips)
            if self._direction == "BUY":
                if self._current_sl == 0.0 or new_sl > self._current_sl:
                    self._current_sl = new_sl
                    return ExitDecision(
                        should_exit=False, action="ADJUST_SL",
                        reason="Exhaustion near structure: tight trail",
                        suggested_sl=new_sl
                    )
            else: # SELL
                if self._current_sl == 0.0 or new_sl < self._current_sl:
                    self._current_sl = new_sl
                    return ExitDecision(
                        should_exit=False, action="ADJUST_SL",
                        reason="Exhaustion near structure: tight trail",
                        suggested_sl=new_sl
                    )

        # Open space — this could be a pullback, not a TP signal
        return None

    def _compute_trail_pips(
        self,
        vel: NormalizedVelocity,
        disp: DisplacementState,
        liq: LiquidityState,
        phase: TradePhase,
        phase_confidence: float,
        atr_pips: float,
    ) -> float:
        cls = disp.classification

        # Trend acceleration in open space → wide trail
        if cls == DISPLACEMENT_IMPULSE and liq.distance_to_nearest_level > 1.2 * atr_pips:
            return atr_pips * (0.8 + (vel.percentile / 100.0) * 0.8)    # 0.8 to 1.6 * ATR

        # Impulse approaching structure → could be final push → tighten
        if cls == DISPLACEMENT_IMPULSE and liq.distance_to_nearest_level <= 0.65 * atr_pips:
            return 0.33 * atr_pips

        # Exhaustion phase → tighten significantly
        if phase == TradePhase.EXHAUSTION or cls == DISPLACEMENT_EXHAUSTION:
            return atr_pips * (0.15 + (1.0 - vel.decay_ratio) * 0.40)       # 0.15 to 0.55 * ATR

        # NOISE (trap/absorption) → moderate trail, do not over-tighten
        # High vel + no displacement = liquidity fight, not exhaustion
        if cls in (DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION):
            return 0.6 * atr_pips

        # Protecting mode (confidence 50–70)
        if phase_confidence < 70.0:
            return 0.4 * atr_pips

        # Default
        return atr_pips * (0.5 + (vel.percentile / 100.0) * 0.5)          # 0.5 to 1.0 * ATR

    def evaluate(
        self,
        current_price: float,
        health: TradeHealth,
        regime: RegimeState,
        liquidity: LiquidityState,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        phase: TradePhase,
        phase_confidence: float,
        exit_stats: ExitStats | None = None,
        mtf: Optional[MTFState] = None,
        atr: Optional[float] = None,
        velocity_health: Optional[TradeVelocityHealth] = None,
    ) -> ExitDecision:
        """Evaluate if we should hold, adjust SL/TP, or force close."""
        if self._ticket == 0:
            return ExitDecision()

        # Convert H1 ATR to pips (needed for intelligent exit)
        atr_pips = (atr / self._pip) if (atr is not None and atr > 0) else 12.0

        # ── PRIORITY 0: Intelligent Velocity-Based Exit (NEW) ──
        if velocity_health is not None:
            pips_profit = (current_price - self._entry_price) / self._pip
            if self._direction == "SELL":
                pips_profit = -pips_profit

            intelligent_decision = self.intelligent_exit.decide_exit(
                velocity_health=velocity_health,
                current_price=current_price,
                entry_price=self._entry_price,
                direction=self._direction,
                pips_profit=pips_profit,
                atr_pips=atr_pips
            )

            # Convert intelligent exit decision to ExitDecision format
            if intelligent_decision.should_close:
                return self._close(intelligent_decision.reason)
            elif intelligent_decision.signal.value == "TIGHT_TRAIL" and intelligent_decision.suggested_sl:
                self._current_sl = intelligent_decision.suggested_sl
                return ExitDecision(
                    should_exit=False,
                    action="ADJUST_SL",
                    reason=intelligent_decision.reason,
                    suggested_sl=intelligent_decision.suggested_sl
                )
            
        pips_profit = (current_price - self._entry_price) / self._pip
        if self._direction == "SELL":
            pips_profit = -pips_profit

        # Convert H1 ATR to pips
        atr_pips = (atr / self._pip) if (atr is not None and atr > 0) else 12.0

        # Pre-evaluation: check breakeven trigger (secure at 50% of stop distance)
        sl_distance_pips = abs(self._entry_price - self._initial_sl) / self._pip
        just_secured_be = False
        if not self._is_breakeven_secured and sl_distance_pips > 0 and pips_profit >= (sl_distance_pips * 0.5):
            self._is_breakeven_secured = True
            just_secured_be = True
            be_price = self._entry_price + (1.0 * self._pip) if self._direction == "BUY" else self._entry_price - (1.0 * self._pip)
            if self._direction == "BUY":
                if self._current_sl == 0.0 or be_price > self._current_sl:
                    self._current_sl = be_price
            else:
                if self._current_sl == 0.0 or be_price < self._current_sl:
                    self._current_sl = be_price

        energy = self._market_energy(velocity, displacement)

        # ── Priority 1 — Adverse Impulse Cut ──
        decision = None
        if energy == "ADVERSE_IMPULSE" and health.is_failing:
            decision = self._close(f"Adverse Impulse Cut ({phase.value if hasattr(phase, 'value') else phase})")

        # ── Priority 2 — Confidence Threshold Exit ──
        elif phase_confidence < 50.0 and energy != "NOISE":
            # Weak confidence + real directional signal against us → exit
            if pips_profit < 0.0:
                decision = self._close(f"Confidence Decay ({phase_confidence:.0f})")

        # ── Priority 3 — Exhaustion TP (location required) ──
        if decision is None:
            exhaustion_dec = self._check_exhaustion_tp(current_price, velocity, displacement, liquidity, pips_profit, phase, atr_pips)
            if exhaustion_dec is not None:
                decision = exhaustion_dec

        # ── Priority 3b — Direct Velocity Decay Exit ──
        if decision is None:
            # Check MTF trend alignment
            is_trend_aligned = False
            if mtf is not None:
                if self._direction == "BUY" and mtf.alignment_score > 0.3:
                    is_trend_aligned = True
                elif self._direction == "SELL" and mtf.alignment_score < -0.3:
                    is_trend_aligned = True

            factor = self.config.get("realtime_velocity_decay_profit_factor", 0.25)
            if getattr(self, "_is_sweep", False) and is_trend_aligned:
                factor *= 4.0  # Require 1.0 * ATR (12-15 pips) profit floor for sweeps to run further
                
            min_profit_limit = factor * atr_pips
            
            decay_thresh_aligned = self.config.get("realtime_velocity_decay_threshold_aligned", 0.20)
            decay_thresh_unaligned = self.config.get("realtime_velocity_decay_threshold_unaligned", 0.40)
            decay_threshold = decay_thresh_aligned if is_trend_aligned else decay_thresh_unaligned
            
            is_decaying_enough = (velocity.decay_ratio < decay_threshold) or (phase == TradePhase.EXHAUSTION)
            
            if pips_profit >= min_profit_limit and is_decaying_enough:
                decision = self._close(f"Velocity Decay Exit (decay={velocity.decay_ratio:.2f}, threshold={decay_threshold:.2f}, aligned={is_trend_aligned})")


        # ── Priority 4 — Health Failure (stagnation / drawdown) ──
        if decision is None and health.is_failing and "Adverse Impulse" not in health.reason:
            # Stagnation / extended drawdown — only cut if not sitting on a profit
            if pips_profit < 0.4 * atr_pips:
                decision = self._close(f"Health: {health.reason}")

        # ── Priority 5 — Velocity Trail ──
        if decision is None:
            trail_pips = self._compute_trail_pips(velocity, displacement, liquidity, phase, phase_confidence, atr_pips)
            trail_dist = trail_pips * self._pip
            if self._direction == "BUY":
                new_sl = current_price - trail_dist
                if self._current_sl == 0.0 or new_sl > self._current_sl:
                    self._current_sl = new_sl
                    decision = ExitDecision(
                        should_exit=False,
                        action="ADJUST_SL",
                        reason=f"Velocity Trail ({trail_pips:.1f}p)",
                        suggested_sl=new_sl
                    )
            else: # SELL
                new_sl = current_price + trail_dist
                if self._current_sl == 0.0 or new_sl < self._current_sl:
                    self._current_sl = new_sl
                    decision = ExitDecision(
                        should_exit=False,
                        action="ADJUST_SL",
                        reason=f"Velocity Trail ({trail_pips:.1f}p)",
                        suggested_sl=new_sl
                    )

        # ── Priority 6 — Breakeven ──
        if decision is None and just_secured_be:
            decision = ExitDecision(
                should_exit=False,
                action="ADJUST_SL",
                reason="Secured Breakeven",
                suggested_sl=self._current_sl
            )

        if decision is None:
            decision = ExitDecision()

        # Record exit stat if it's an exit
        if decision.should_exit and exit_stats is not None:
            exit_stats.record(
                reason=decision.reason,
                pips=pips_profit,
                phase=phase.value if hasattr(phase, 'value') else str(phase),
                confidence=phase_confidence,
                energy_state=energy,
                pips_profit_at_exit=pips_profit
            )

        return decision


__all__ = ["AdaptiveExitManager", "ExitDecision"]
