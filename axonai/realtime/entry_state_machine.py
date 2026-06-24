"""Entry State Machine.

Replaces the legacy stateless boolean EntryGate. Implements a 5-state
machine to prevent entering too early on the raw microstructure peak,
forcing price to confirm direction via displacement first.

States:
0. IDLE        - Waiting for microstructure anomaly
1. ANOMALY     - Velocity/Volume spike detected (Peak)
2. ARMING      - Displacement failing (Trap/Absorption forming)
3. TRIGGERED   - Genuine structural break away from the trap
4. INVALIDATED - Anomaly disappeared, trap failed, or timeout

Only State 3 returns a valid "BUY"/"SELL" signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState, DISPLACEMENT_IMPULSE, DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState, REGIME_RANGE_CHOP, REGIME_EXHAUSTION, REGIME_REVERSAL


# ── Entry States ─────────────────────────────────────────────────────────
STATE_IDLE = "IDLE"
STATE_ANOMALY = "ANOMALY"
STATE_ARMING = "ARMING"
STATE_TRIGGERED = "TRIGGERED"
STATE_INVALIDATED = "INVALIDATED"


@dataclass
class EntryDecision:
    """The output of the entry state machine."""
    state: str = STATE_IDLE
    is_valid_entry: bool = False
    direction: Optional[str] = None    # "BUY" or "SELL"
    signal_quality: float = 0.0        # 0.0 to 1.0
    reason: str = "Awaiting anomaly"


class EntryStateMachine:
    """Stateful trade entry execution manager."""

    def __init__(self, timeout_sec: float = 120.0, pip_mult: float = 0.0001):
        self._pip = pip_mult
        self._timeout_sec = timeout_sec
        
        # State tracking
        self._current_state = STATE_IDLE
        self._anomaly_time: float = 0.0
        self._anomaly_price: float = 0.0
        self._anomaly_direction: str = ""   # Direction we expect the reversal
        self._anomaly_type: str = ""        # "sweep" or "climax"
        self._max_adverse_excursion: float = 0.0
        self._last_tick_time: float = 0.0
        
        # Diagnostic
        self._last_reason = "Initialized"

    def reset(self) -> None:
        """Force the machine back to IDLE."""
        self._current_state = STATE_IDLE
        self._anomaly_time = 0.0
        self._anomaly_price = 0.0
        self._anomaly_direction = ""
        self._anomaly_type = ""
        self._max_adverse_excursion = 0.0
        self._last_tick_time = 0.0
        self._last_reason = "Reset"

    def evaluate(
        self,
        price: float,
        timestamp: datetime,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        liquidity: LiquidityState,
        regime: RegimeState,
        mtf: MTFState,
    ) -> EntryDecision:
        """Evaluate conditions and transition states."""
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
        
        # Adjust anomaly time if there was a large gap between ticks (e.g. candle gap in backtest)
        if self._last_tick_time > 0.0 and self._anomaly_time > 0.0:
            gap = ts - self._last_tick_time
            if gap > 5.0:
                self._anomaly_time += gap
                logger.debug("EntryStateMachine: Adjusted anomaly_time by %.1f seconds due to tick gap", gap)
                
        self._last_tick_time = ts
        
        # 1. Timeout Check
        if self._current_state not in (STATE_IDLE, STATE_INVALIDATED):
            elapsed = ts - self._anomaly_time
            if elapsed > self._timeout_sec:
                self._transition(STATE_INVALIDATED, f"Timeout after {elapsed:.1f}s")
                
        # 2. State Machine Transitions
        if self._current_state in (STATE_IDLE, STATE_INVALIDATED):
            self._evaluate_idle(price, ts, velocity, displacement, liquidity, regime)
            
        elif self._current_state == STATE_ANOMALY:
            self._evaluate_anomaly(price, ts, velocity, displacement)
            
        elif self._current_state == STATE_ARMING:
            self._evaluate_arming(price, ts, displacement, mtf)
            
        elif self._current_state == STATE_TRIGGERED:
            # Linger in triggered state until explicitly reset by TradeExecutor
            pass

        # 3. Decision generation
        is_trigger = self._current_state == STATE_TRIGGERED
        quality = self._calculate_quality(regime, mtf) if is_trigger else 0.0
        
        reason = self._last_reason
        if is_trigger:
            reason = f"Displacement away from trap confirmed ({self._anomaly_type})"
            
        return EntryDecision(
            state=self._current_state,
            is_valid_entry=is_trigger,
            direction=self._anomaly_direction if is_trigger else None,
            signal_quality=round(quality, 2),
            reason=reason
        )

    def _transition(self, new_state: str, reason: str) -> None:
        old_state = self._current_state
        self._current_state = new_state
        self._last_reason = reason
        logger.info("EntryStateMachine: Transition %s -> %s | Reason: %s", old_state, new_state, reason)

    def _evaluate_idle(
        self, price: float, ts: float, vel: NormalizedVelocity,
        disp: DisplacementState, liq: LiquidityState, regime: RegimeState
    ) -> None:
        """Look for the initial anomaly (Microstructure Peak)."""
        # Anomaly criteria: High velocity + low tick efficiency (Climax)
        is_climax = vel.is_unusual and vel.tick_efficiency < 0.2
        
        # Or an active liquidity sweep
        is_sweep = len(liq.active_sweeps) > 0
        
        # Infer expected reversal direction
        direction = ""
        if is_sweep:
            # Sweeping support -> expect BUY; sweeping resistance -> expect SELL
            sweep_lvl = liq.active_sweeps[0]
            if price > sweep_lvl.price:
                direction = "BUY"
            else:
                direction = "SELL"
        elif is_climax:
            # Bullish climax (net displacement positive) -> expect SELL reversal
            if disp.net_displacement_pips > 0:
                direction = "SELL"
            # Bearish climax (net displacement negative) -> expect BUY reversal
            elif disp.net_displacement_pips < 0:
                direction = "BUY"
            
        if (is_climax or is_sweep) and direction:
            self._anomaly_time = ts
            self._anomaly_price = price
            self._anomaly_direction = direction
            self._anomaly_type = "sweep" if is_sweep else "climax"
            self._max_adverse_excursion = 0.0

            reason = "Sweep detected" if is_sweep else "Microstructure climax"
            logger.info(
                "EntryStateMachine ANOMALY detected: price=%.5f direction=%s type=%s (climax: vel_unusual=%s eff=%.2f)",
                price, direction, self._anomaly_type, vel.is_unusual, vel.tick_efficiency
            )
            self._transition(STATE_ANOMALY, f"{reason}. Expected reversal: {direction}")

    def _evaluate_anomaly(
        self, price: float, ts: float, vel: NormalizedVelocity, disp: DisplacementState
    ) -> None:
        """Wait for the anomaly to form a trap or show absorption."""
        # Update adverse excursion
        dist = (price - self._anomaly_price) / self._pip
        if self._anomaly_direction == "SELL" and dist > self._max_adverse_excursion:
            self._max_adverse_excursion = dist
        elif self._anomaly_direction == "BUY" and -dist > self._max_adverse_excursion:
            self._max_adverse_excursion = -dist

        # Invalidate if it pushes too far against us without absorption
        if self._max_adverse_excursion > 5.0 and disp.classification == DISPLACEMENT_IMPULSE:
            self._transition(STATE_INVALIDATED, "Anomaly broken by strong impulse")
            return

        # Arm if we see trap or absorption logic
        if disp.classification in (DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION):
            self._transition(STATE_ARMING, "Absorption confirmed. Arming trigger.")
        # Or arm if velocity completely decays (exhaustion)
        elif vel.is_decaying:
            self._transition(STATE_ARMING, "Velocity decayed. Arming trigger.")

    def _evaluate_arming(
        self, price: float, ts: float, disp: DisplacementState, mtf: MTFState
    ) -> None:
        """Wait for the price to break away from the trap in our direction."""
        dist = (price - self._anomaly_price) / self._pip

        # Trigger criteria: Impulse, or high displacement ratio, or exhaustion (velocity decay = momentum shift)
        is_impulse = (
            (disp.classification == DISPLACEMENT_IMPULSE) or
            (disp.displacement_ratio > 0.5) or
            (disp.classification == "EXHAUSTION")  # Velocity decay = potential reversal
        )

        is_trigger = False
        # Relaxed thresholds for live market microstructure (was 1.5 pips, 0.5 ratio)
        if self._anomaly_direction == "SELL" and dist < -0.5 and is_impulse:
            is_trigger = True
        elif self._anomaly_direction == "BUY" and dist > 0.5 and is_impulse:
            is_trigger = True

        # Debug logging for trigger condition
        logger.info(
            "EntryStateMachine ARMING check: dir=%s dist=%.2f is_impulse=%s (class=%s ratio=%.2f) trigger=%s",
            self._anomaly_direction, dist, is_impulse, disp.classification, disp.displacement_ratio, is_trigger
        )
            
        if is_trigger:
            # Final safety check against MTF
            is_trend_blocked = False
            if self._anomaly_direction == "BUY" and mtf.h1_bias < 0.0 and mtf.h4_bias < 0.0:
                is_trend_blocked = True
            elif self._anomaly_direction == "SELL" and mtf.h1_bias > 0.0 and mtf.h4_bias > 0.0:
                is_trend_blocked = True

            if is_trend_blocked:
                self._transition(STATE_INVALIDATED, f"Blocked: Trend filter (H1={mtf.h1_bias:.2f}, H4={mtf.h4_bias:.2f})")
            elif (self._anomaly_direction == "BUY" and (mtf.h1_bias < -0.4 or mtf.h4_bias < -0.4)) or \
                 (self._anomaly_direction == "SELL" and (mtf.h1_bias > 0.4 or mtf.h4_bias > 0.4)):
                self._transition(STATE_INVALIDATED, f"Blocked: Trading against strong trend (H1={mtf.h1_bias:.2f}, H4={mtf.h4_bias:.2f})")
            else:
                self._transition(STATE_TRIGGERED, "Displacement away from trap confirmed.")

    def _calculate_quality(self, regime: RegimeState, mtf: MTFState) -> float:
        """Calculate a 0.0 to 1.0 confidence score for the entry."""
        score = 0.5 # Base
        
        # Regime alignment
        if regime.regime in (REGIME_REVERSAL, REGIME_EXHAUSTION):
            score += 0.2
        elif regime.regime == REGIME_RANGE_CHOP:
            score += 0.1
            
        # MTF Context
        if self._anomaly_direction == "BUY" and mtf.alignment_score > 0.3:
            score += 0.2
        elif self._anomaly_direction == "SELL" and mtf.alignment_score < -0.3:
            score += 0.2
        elif mtf.is_pullback:
            score += 0.1 # Reversing a pullback into the main trend is good
            
        return min(max(score, 0.0), 1.0)


__all__ = [
    "EntryStateMachine",
    "EntryDecision",
    "STATE_IDLE",
    "STATE_ANOMALY",
    "STATE_ARMING",
    "STATE_TRIGGERED",
    "STATE_INVALIDATED",
]
