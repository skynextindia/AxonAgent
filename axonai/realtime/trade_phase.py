"""Trade Phase Tracker.

Tracks the lifecycle of an open position through market-state-driven
phase transitions. No time-based transitions — all driven by:
  - DisplacementEngine classification
  - VelocityNormalizer state
  - LiquidityEngine context
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import (
    DisplacementState,
    DISPLACEMENT_IMPULSE, DISPLACEMENT_EXHAUSTION,
    DISPLACEMENT_COMPRESSION, DISPLACEMENT_TRAP,
    DISPLACEMENT_ABSORPTION, DISPLACEMENT_NEUTRAL,
)
from axonai.realtime.liquidity_engine import LiquidityState


class TradePhase(str, Enum):
    WAITING         = "WAITING"           # no position open
    ENTRY_TRIGGERED = "ENTRY_TRIGGERED"   # just opened — short grace period
    EXPANSION       = "EXPANSION"         # moving our direction, early
    CONTINUATION    = "CONTINUATION"      # sustained IMPULSE — trend confirmed
    COMPRESSION     = "COMPRESSION"       # neutral pause — energy rebuilding
    EXHAUSTION      = "EXHAUSTION"        # move slowing (EXHAUSTION classification)
    REVERSAL_RISK   = "REVERSAL_RISK"     # adverse IMPULSE detected


@dataclass
class PhaseSnapshot:
    phase: TradePhase = TradePhase.WAITING
    confidence: float = 100.0            # 0–100
    consecutive_impulse: int = 0         # ticks with same-direction IMPULSE
    reason: str = "Initialized"


class TradePhaseTracker:
    """Tracks trade lifecycle phase and confidence score.

    Call register_trade() on entry.
    Call update() on every tick.
    Call clear() on exit.
    """

    CONFIDENCE_HOLD    = 70.0
    CONFIDENCE_PROTECT = 50.0

    def __init__(self, pip_mult: float = 0.0001):
        self._pip = pip_mult
        self._phase = TradePhase.WAITING
        self._confidence = 100.0
        self._direction = ""
        self._entry_price = 0.0
        self._consecutive_impulse = 0
        self._consecutive_adverse_impulse = 0
        self._last_classification = DISPLACEMENT_NEUTRAL
        self._last_transition_reason = "Initialized"

    def register_trade(
        self,
        direction: str,
        entry_price: float,
        initial_confidence: float = 80.0,   # from EntryDecision.signal_quality * 100
    ) -> None:
        self._direction = direction.upper()
        self._entry_price = entry_price
        self._confidence = max(0.0, min(100.0, initial_confidence))
        self._consecutive_impulse = 0
        self._consecutive_adverse_impulse = 0
        self._last_classification = DISPLACEMENT_NEUTRAL
        self._last_transition_reason = "Initialized"
        self._phase = TradePhase.ENTRY_TRIGGERED

    def update(
        self,
        current_price: float,
        vel: NormalizedVelocity,
        disp: DisplacementState,
        liq: LiquidityState,
    ) -> PhaseSnapshot:
        if self._phase == TradePhase.WAITING:
            return PhaseSnapshot(TradePhase.WAITING, self._confidence)

        pips_profit = (current_price - self._entry_price) / self._pip
        if self._direction == "SELL":
            pips_profit = -pips_profit

        self._update_consecutive_impulse(disp)
        self._transition(pips_profit, vel, disp, liq)
        self._update_confidence(pips_profit, vel, disp)

        return PhaseSnapshot(
            phase=self._phase,
            confidence=round(self._confidence, 1),
            consecutive_impulse=self._consecutive_impulse,
            reason=self._last_transition_reason,
        )

    def clear(self) -> None:
        self._phase = TradePhase.WAITING
        self._confidence = 100.0
        self._direction = ""
        self._consecutive_impulse = 0
        self._consecutive_adverse_impulse = 0

    @property
    def phase(self) -> TradePhase:
        return self._phase

    @property
    def confidence(self) -> float:
        return self._confidence

    # ── Transition Logic ──────────────────────────────────────────────

    def _transition(self, pips_profit: float, vel: NormalizedVelocity, disp: DisplacementState, liq: LiquidityState) -> None:
        cls = disp.classification
        phase = self._phase

        # ── ENTRY_TRIGGERED ──────────────────────────────────────────
        if phase == TradePhase.ENTRY_TRIGGERED:
            # Transition on strong impulse in our direction even if pips_profit is negative,
            # or if any non-neutral displacement with positive profit.
            is_strong_favorable_impulse = (cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp))
            if (is_strong_favorable_impulse) or (cls != DISPLACEMENT_NEUTRAL and pips_profit >= 0):
                self._set_phase(TradePhase.EXPANSION, f"Initial displacement detected ({cls})")

        # ── EXPANSION ────────────────────────────────────────────────
        elif phase == TradePhase.EXPANSION:
            if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp) and \
               self._consecutive_impulse >= 2:
                self._set_phase(TradePhase.CONTINUATION, "Sustained IMPULSE confirmed")
            elif cls == DISPLACEMENT_EXHAUSTION:
                self._set_phase(TradePhase.EXHAUSTION, "EXHAUSTION during EXPANSION")

        # ── CONTINUATION ─────────────────────────────────────────────
        elif phase == TradePhase.CONTINUATION:
            if cls == DISPLACEMENT_EXHAUSTION:
                self._set_phase(TradePhase.EXHAUSTION, "EXHAUSTION after CONTINUATION")
            elif cls in (DISPLACEMENT_COMPRESSION, DISPLACEMENT_NEUTRAL):
                self._set_phase(TradePhase.COMPRESSION, "Energy compressing after trend")

        # ── COMPRESSION ──────────────────────────────────────────────
        elif phase == TradePhase.COMPRESSION:
            if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp):
                self._set_phase(TradePhase.CONTINUATION, "Impulse resumed after compression")
            elif cls == DISPLACEMENT_EXHAUSTION:
                self._set_phase(TradePhase.EXHAUSTION, "EXHAUSTION after compression")

        # ── EXHAUSTION ───────────────────────────────────────────────
        elif phase == TradePhase.EXHAUSTION:
            # Can recover if a fresh impulse in our direction fires
            if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp) and \
               self._consecutive_impulse >= 2:
                self._set_phase(TradePhase.CONTINUATION, "Impulse resumed from EXHAUSTION")

        # ── REVERSAL_RISK ─────────────────────────────────────────────
        elif phase == TradePhase.REVERSAL_RISK:
            # Recover if a fresh impulse in our direction is sustained
            if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp) and \
               self._consecutive_impulse >= 2:
                self._set_phase(TradePhase.CONTINUATION, "Impulse resumed from REVERSAL_RISK")
            # Or if pressure subsides (neutral or compression)
            elif cls in (DISPLACEMENT_COMPRESSION, DISPLACEMENT_NEUTRAL):
                self._set_phase(TradePhase.COMPRESSION, "Pressure subsided after reversal risk")

        # ── Any phase: detect adverse IMPULSE → REVERSAL_RISK ────────
        if self._phase != TradePhase.ENTRY_TRIGGERED and \
           self._consecutive_adverse_impulse >= 5:
            self._set_phase(TradePhase.REVERSAL_RISK, "Adverse IMPULSE detected")

    # ── Confidence Decay / Recovery ───────────────────────────────────

    def _update_confidence(self, pips_profit: float, vel: NormalizedVelocity, disp: DisplacementState) -> None:
        if self._phase == TradePhase.ENTRY_TRIGGERED:
            # Grace period: no confidence decay while waiting for entry to develop
            return

        cls = disp.classification

        if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp):
            self._confidence = min(100.0, self._confidence + 2.0)   # recover

        elif cls == DISPLACEMENT_IMPULSE and not self._in_our_direction(disp):
            self._confidence -= 3.0   # adverse impulse — reasonable drop, not instant exit

        elif cls == DISPLACEMENT_EXHAUSTION:
            self._confidence -= 1.0

        # We do not penalize confidence on simple velocity decay (vel.is_decaying)
        # as consolidation/breathing is natural.

        if self._phase == TradePhase.REVERSAL_RISK:
            self._confidence -= 1.5   # steady drain under sustained adverse pressure

        self._confidence = max(0.0, min(100.0, self._confidence))

    # ── Helpers ───────────────────────────────────────────────────────

    def _in_our_direction(self, disp: DisplacementState) -> bool:
        if self._direction == "BUY":
            return disp.net_displacement_pips > 0
        return disp.net_displacement_pips < 0

    def _update_consecutive_impulse(self, disp: DisplacementState) -> None:
        cls = disp.classification
        if cls == DISPLACEMENT_IMPULSE and self._in_our_direction(disp):
            self._consecutive_impulse += 1
            self._consecutive_adverse_impulse = 0
        elif cls == DISPLACEMENT_IMPULSE and not self._in_our_direction(disp):
            self._consecutive_impulse = 0
            self._consecutive_adverse_impulse += 1
        else:
            self._consecutive_impulse = 0
            self._consecutive_adverse_impulse = 0

    def _set_phase(self, new_phase: TradePhase, reason: str) -> None:
        if new_phase != self._phase:
            self._last_transition_reason = reason
            self._phase = new_phase


__all__ = ["TradePhaseTracker", "TradePhase", "PhaseSnapshot"]
