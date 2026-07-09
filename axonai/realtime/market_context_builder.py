"""MarketContextBuilder: Calculate quality scores for MarketContext.

This module computes the confidence scores, consensus verdicts, and signal agreement
that add context layers to raw engine outputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from axonai.realtime.market_context import MarketContext, build_market_context_summary
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.location_engine import LocationContext
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import (
    RegimeState,
    REGIME_TREND_EXPANSION,
    REGIME_TREND_CONTINUATION,
    REGIME_RANGE_CHOP,
    REGIME_COMPRESSION,
    REGIME_BREAKOUT,
)


class MarketContextBuilder:
    """Build MarketContext with quality scores from engine outputs."""

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._last_displacement_state = None
        self._ticks_in_current_displacement = 0

    def build(
        self,
        timestamp: datetime,
        price: float,
        bid: float,
        ask: float,
        volume: int,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        liquidity: LiquidityState,
        location: LocationContext,
        mtf: MTFState,
        regime: RegimeState,
    ) -> MarketContext:
        """Assemble MarketContext with all quality scores calculated."""

        # Track displacement phase progression
        self._update_displacement_phase(displacement)

        # Calculate quality scores
        agreement_score, signals_agree, signals_disagree = self._calculate_signal_agreement(
            velocity, displacement, location, mtf, regime, liquidity
        )

        stop_hunt_detected, stop_hunt_severity, stop_hunt_phase = self._detect_stop_hunting(
            price, liquidity, displacement
        )

        reversal_confidence = self._calculate_reversal_confidence(
            velocity, displacement, location, mtf, regime, agreement_score
        )

        consensus_verdict = self._determine_consensus_verdict(reversal_confidence, agreement_score)

        lag_ticks, is_lagged, lag_severity = self._estimate_reversal_lag(
            velocity, displacement, self._ticks_in_current_displacement
        )

        # Build context
        ctx = MarketContext(
            timestamp=timestamp,
            price=price,
            bid=bid,
            ask=ask,
            volume=volume,
            velocity=velocity,
            displacement=displacement,
            liquidity=liquidity,
            location=location,
            mtf=mtf,
            regime=regime,
            # Quality scores
            signal_agreement_score=agreement_score,
            signals_that_agree=signals_agree,
            signals_that_disagree=signals_disagree,
            stop_hunt_detected=stop_hunt_detected,
            stop_hunt_severity=stop_hunt_severity,
            stop_hunt_phase=stop_hunt_phase,
            reversal_confidence=reversal_confidence,
            consensus_verdict=consensus_verdict,
            displacement_phase=self._displacement_phase_str,
            reversal_lag_ticks=lag_ticks,
            is_lagged=is_lagged,
            lag_severity=lag_severity,
            entry_window_closing=self._is_entry_window_closing(
                reversal_confidence, lag_ticks, self._ticks_in_current_displacement
            ),
            ticks_until_confirmation_expires=self._ticks_until_expiration(
                displacement, self._ticks_in_current_displacement
            ),
            summary=build_market_context_summary(MarketContext(
                timestamp=timestamp, price=price, bid=bid, ask=ask, volume=volume,
                velocity=velocity, displacement=displacement, liquidity=liquidity,
                location=location, mtf=mtf, regime=regime,
                signal_agreement_score=agreement_score,
                reversal_confidence=reversal_confidence,
                consensus_verdict=consensus_verdict,
                displacement_phase=self._displacement_phase_str,
            )),
        )

        return ctx

    # ========== SIGNAL AGREEMENT SCORING ==========

    def _calculate_signal_agreement(
        self,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        location: LocationContext,
        mtf: MTFState,
        regime: RegimeState,
        liquidity: LiquidityState,
    ) -> Tuple[float, List[str], List[str]]:
        """Calculate what % of engines agree on reversal.

        Returns: (agreement_score_0_to_100, agreeing_signals, disagreeing_signals)
        """
        signals_agree = []
        signals_disagree = []

        # Signal 1: Velocity Engine
        if velocity.percentile > 70 or velocity.is_unusual:
            signals_agree.append("VELOCITY_SPIKE")
        else:
            signals_disagree.append("VELOCITY_QUIET")

        # Signal 2: Displacement Engine
        if displacement.classification in ("IMPULSE", "EXHAUSTION"):
            signals_agree.append("DISPLACEMENT_DIRECTIONAL")
        elif displacement.classification in ("TRAP", "ABSORPTION"):
            signals_agree.append("DISPLACEMENT_SETUP")
        else:
            signals_disagree.append("DISPLACEMENT_NEUTRAL")

        # Signal 3: Location Engine (support/resistance alignment)
        if location.at_structure and location.distance_to_sr < 5.0:
            signals_agree.append("LOCATION_AT_KEY_LEVEL")
        else:
            signals_disagree.append("LOCATION_RANDOM")

        # Signal 4: Regime Engine
        if regime.regime in (REGIME_TREND_EXPANSION, REGIME_TREND_CONTINUATION, REGIME_BREAKOUT):
            signals_agree.append("REGIME_DIRECTIONAL")
        elif regime.regime in (REGIME_RANGE_CHOP, REGIME_COMPRESSION):
            signals_disagree.append("REGIME_CHOPPY")
        else:
            signals_disagree.append("REGIME_UNCERTAIN")

        # Signal 5: Multi-Timeframe Alignment
        if abs(mtf.h1_bias) > 30 or abs(mtf.h4_bias) > 30:
            signals_agree.append("MTF_ALIGNED")
        elif abs(mtf.h1_bias) < 10 and abs(mtf.h4_bias) < 10:
            signals_disagree.append("MTF_CONFLICTED")
        else:
            signals_disagree.append("MTF_NEUTRAL")

        # Signal 6: Liquidity Engine
        if liquidity.active_sweeps:
            signals_agree.append("LIQUIDITY_SWEEP_DETECTED")
        else:
            signals_disagree.append("LIQUIDITY_QUIET")

        # Calculate percentage agreement
        total_signals = len(signals_agree) + len(signals_disagree)
        agreement_pct = (len(signals_agree) / total_signals * 100) if total_signals > 0 else 0

        return agreement_pct, signals_agree, signals_disagree

    # ========== REVERSAL CONFIDENCE SCORING ==========

    def _calculate_reversal_confidence(
        self,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        location: LocationContext,
        mtf: MTFState,
        regime: RegimeState,
        agreement_score: float,
    ) -> float:
        """Score 0-100: How clear/strong is the reversal signal?

        Multi-factor scoring:
        - Velocity spike strength (0-25 points)
        - Displacement classification quality (0-25 points)
        - Location at key level (0-20 points)
        - Regime alignment (0-20 points)
        - Multi-timeframe consensus (0-10 points)
        """
        score = 0.0

        # Factor 1: Velocity (0-25 points)
        # High percentile + unusual = more confident
        if velocity.percentile > 90:
            score += 25
        elif velocity.percentile > 75:
            score += 20
        elif velocity.percentile > 50 and velocity.is_unusual:
            score += 15
        elif velocity.percentile > 50:
            score += 10
        else:
            score += 5

        # Factor 2: Displacement (0-25 points)
        # IMPULSE is most confident, TRAP/ABSORPTION in middle, NEUTRAL worst
        if displacement.classification == "IMPULSE":
            score += 25
        elif displacement.classification == "EXHAUSTION":
            score += 22
        elif displacement.classification in ("TRAP", "ABSORPTION"):
            score += 15
        elif displacement.classification == "NEUTRAL":
            score += 5
        else:
            score += 0

        # Factor 3: Location at key level (0-20 points)
        if location.at_structure:
            if location.distance_to_sr < 2.0:
                score += 20
            elif location.distance_to_sr < 5.0:
                score += 15
            else:
                score += 10
        else:
            score += 3

        # Factor 4: Regime alignment (0-20 points)
        if regime.regime in (REGIME_TREND_EXPANSION, REGIME_TREND_CONTINUATION, REGIME_BREAKOUT):
            score += 20
        elif regime.regime == REGIME_COMPRESSION:
            score += 10
        elif regime.regime == REGIME_RANGE_CHOP:
            score += 5
        else:
            score += 0

        # Factor 5: Multi-timeframe (0-10 points)
        alignment_strength = abs(mtf.h1_bias) + abs(mtf.h4_bias)
        if alignment_strength > 60:
            score += 10
        elif alignment_strength > 40:
            score += 7
        elif alignment_strength > 20:
            score += 4
        else:
            score += 1

        # Clamp to 0-100
        return max(0.0, min(100.0, score))

    # ========== STOP HUNTING DETECTION ==========

    def _detect_stop_hunting(
        self,
        current_price: float,
        liquidity: LiquidityState,
        displacement: DisplacementState,
    ) -> Tuple[bool, float, str]:
        """Detect if stops are being hunted/manipulated.

        Returns: (stop_hunt_detected, severity_0_to_100, phase)
        """
        severity = 0.0
        phase = "NORMAL"

        # Heuristic 1: Active sweeps with low net displacement
        if liquidity.active_sweeps:
            if displacement.displacement_ratio < 0.3:  # Many ticks, little displacement = wicking
                severity += 40
                phase = "HUNTING"

        # Heuristic 2: Sudden reversal after directional move
        _exh_net_max = self._config.get("context_exhaustion_net_max_pips", 2.0)
        if displacement.classification == "EXHAUSTION" and displacement.net_displacement_pips < _exh_net_max:
            severity += 30
            phase = "SWEEPING"

        # Heuristic 3: Multiple rejection patterns at support/resistance
        # (In real implementation, track level attack/rejection counts)
        # For now, use proxy: if at structure and volume spike = likely stop hunt
        if liquidity.active_sweeps and len(liquidity.active_sweeps) > 1:
            severity += 20
            phase = "REVERSING"

        stop_hunt_detected = severity > 30
        return stop_hunt_detected, min(100.0, severity), phase

    # ========== REVERSAL LAG DETECTION ==========

    def _estimate_reversal_lag(
        self, velocity: NormalizedVelocity, displacement: DisplacementState, ticks_in_phase: int
    ) -> Tuple[int, bool, str]:
        """Estimate how many ticks the reversal signal is lagged.

        A lagged reversal = signal triggered 3+ ticks ago, price already moved.
        """
        lag_ticks = 0
        is_lagged = False
        lag_severity = "NONE"

        # If displacement just changed (tick 0-1), no lag
        if ticks_in_phase <= 1:
            lag_ticks = 0
            is_lagged = False
            lag_severity = "NONE"
        # If displacement has been same for 2-3 ticks, slight lag
        elif ticks_in_phase <= 3:
            lag_ticks = ticks_in_phase
            is_lagged = False
            lag_severity = "NONE"
        # If 4+ ticks in same displacement, signal is lagged
        else:
            lag_ticks = ticks_in_phase
            is_lagged = True
            lag_severity = "HEAVY" if ticks_in_phase > 6 else "LIGHT"

        return lag_ticks, is_lagged, lag_severity

    # ========== CONSENSUS VERDICT ==========

    def _determine_consensus_verdict(self, confidence: float, agreement: float) -> str:
        """Determine overall market context verdict.

        Matrix:
        - confidence 80+ AND agreement 80+ → STRONG_REVERSAL
        - confidence 60-79 AND agreement 60+ → MODERATE_REVERSAL
        - confidence 40-59 → WEAK_REVERSAL
        - confidence <40 → AMBIGUOUS
        """
        if confidence >= 80 and agreement >= 75:
            return "STRONG_REVERSAL"
        elif confidence >= 60 and agreement >= 60:
            return "MODERATE_REVERSAL"
        elif confidence >= 40:
            return "WEAK_REVERSAL"
        elif agreement < 30:
            return "RANGE_CHOP"
        else:
            return "AMBIGUOUS"

    # ========== DISPLACEMENT PHASE TRACKING ==========

    def _update_displacement_phase(self, displacement: DisplacementState) -> None:
        """Track how long displacement has been in current classification."""
        if self._last_displacement_state is None:
            self._ticks_in_current_displacement = 0
            self._displacement_phase_str = "EARLY"
        elif displacement.classification != self._last_displacement_state.classification:
            # Classification changed
            self._ticks_in_current_displacement = 0
            self._displacement_phase_str = "EARLY"
        else:
            # Same classification
            self._ticks_in_current_displacement += 1

            if self._ticks_in_current_displacement == 0:
                self._displacement_phase_str = "EARLY"
            elif self._ticks_in_current_displacement <= 2:
                self._displacement_phase_str = "CONFIRMING"
            else:
                self._displacement_phase_str = "CONFIRMED"

        self._last_displacement_state = displacement

    # ========== ENTRY TIMING & EXPIRATION ==========

    def _is_entry_window_closing(self, confidence: float, lag_ticks: int, ticks_in_phase: int) -> bool:
        """Is the reversal opportunity closing/expiring?

        Windows close when:
        - Lag is >5 ticks (signal very delayed, opportunity passed)
        - Confidence dropping (reversal weakening)
        - Ticks in phase >10 (setup breaking down)
        """
        if lag_ticks > 5:
            return True
        if ticks_in_phase > 10:
            return True
        if confidence < 30:  # Signal weakening
            return True
        return False

    def _ticks_until_expiration(self, displacement: DisplacementState, ticks_in_phase: int) -> int:
        """How many ticks until this signal becomes stale/invalid?

        Signals expire based on displacement classification:
        - IMPULSE: 15 ticks (strong signal, lasts longer)
        - TRAP/ABSORPTION: 8 ticks (needs confirmation)
        - NEUTRAL: 3 ticks (weak, expires fast)
        """
        max_ticks = {
            "IMPULSE": 15,
            "EXHAUSTION": 12,
            "TRAP": 8,
            "ABSORPTION": 8,
            "NEUTRAL": 3,
        }

        max_allowed = max_ticks.get(displacement.classification, 5)
        remaining = max(0, max_allowed - ticks_in_phase)

        return remaining
