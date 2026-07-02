"""Market regime classification engine.

Classifies the current market state into one of 7 regimes using
velocity context, displacement metrics, and candle structure.
Key difference from the old system: the same velocity value means
different things depending on the regime.

Regimes:
  TREND_EXPANSION   — strong directional move
  TREND_CONTINUATION — existing trend + moderate velocity
  RANGE_CHOP        — no trend + oscillating displacement
  COMPRESSION       — declining volatility + squeeze
  BREAKOUT          — range expansion after compression
  EXHAUSTION        — velocity decaying, displacement divergence
  REVERSAL          — multi-factor reversal confirmed
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, TYPE_CHECKING

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.event_types import LiveCandle

if TYPE_CHECKING:
    from axonai.realtime.displacement_engine import DisplacementState


# ── Regime constants ────────────────────────────────────────────────
REGIME_TREND_EXPANSION = "TREND_EXPANSION"
REGIME_TREND_CONTINUATION = "TREND_CONTINUATION"
REGIME_RANGE_CHOP = "RANGE_CHOP"
REGIME_COMPRESSION = "COMPRESSION"
REGIME_BREAKOUT = "BREAKOUT"
REGIME_EXHAUSTION = "EXHAUSTION"
REGIME_REVERSAL = "REVERSAL"

ALL_REGIMES = [
    REGIME_TREND_EXPANSION,
    REGIME_TREND_CONTINUATION,
    REGIME_RANGE_CHOP,
    REGIME_COMPRESSION,
    REGIME_BREAKOUT,
    REGIME_EXHAUSTION,
    REGIME_REVERSAL,
]


@dataclass
class RegimeState:
    """Output snapshot of the regime engine."""

    regime: str = REGIME_RANGE_CHOP
    confidence: float = 0.5
    scores: Dict[str, float] = field(default_factory=dict)

    # What velocity means in this regime
    velocity_context: str = ""

    # Transition awareness
    transition_probability: float = 0.0
    previous_regime: str = ""
    bars_in_regime: int = 0

    # Trend metadata
    trend_direction: str = "sideways"  # up / down / sideways
    trend_strength: float = 0.0       # 0-1


class RegimeEngine:
    """Classifies market regime from velocity, displacement, and candle data.

    Update on every M15 candle close (not every tick) to avoid noise.
    Optionally update on tick for real-time regime transition detection.
    """

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        # Late import to avoid circular dependency
        from axonai.realtime.displacement_engine import (
            DISPLACEMENT_IMPULSE,
            DISPLACEMENT_EXHAUSTION,
            DISPLACEMENT_COMPRESSION,
            DISPLACEMENT_TRAP,
        )

        self.DISPLACEMENT_IMPULSE = DISPLACEMENT_IMPULSE
        self.DISPLACEMENT_EXHAUSTION = DISPLACEMENT_EXHAUSTION
        self.DISPLACEMENT_COMPRESSION = DISPLACEMENT_COMPRESSION
        self.DISPLACEMENT_TRAP = DISPLACEMENT_TRAP

        self._pip = pip_mult
        self._config = config or {}

        # Rolling candle history for structure analysis
        self._candles: deque[LiveCandle] = deque(maxlen=200)

        # EMA state (20-period for M15)
        self._ema_20: Optional[float] = None
        self._ema_50: Optional[float] = None
        self._ema_k20 = 2.0 / 21.0
        self._ema_k50 = 2.0 / 51.0

        # ATR (14-period)
        self._atr_values: deque[float] = deque(maxlen=20)
        self._atr_14: float = 0.0

        # Bollinger band width (for compression detection)
        self._bb_width_history: deque[float] = deque(maxlen=30)

        # Regime persistence
        self._current_regime = REGIME_RANGE_CHOP
        self._regime_bars = 0
        self._previous_regime = ""

        # Rolling regime scores for smoothing
        self._regime_score_history: deque[Dict[str, float]] = deque(maxlen=5)

    def update(
        self,
        candle: LiveCandle,
        velocity: NormalizedVelocity,
        displacement: "DisplacementState",
    ) -> RegimeState:
        """Classify regime on a new candle close.

        Args:
            candle: Just-closed M15 candle
            velocity: Latest NormalizedVelocity snapshot
            displacement: Latest DisplacementState snapshot

        Returns:
            RegimeState with classification and context.
        """
        self._candles.append(candle)
        self._update_indicators(candle)

        if len(self._candles) < 20:
            return RegimeState(regime=REGIME_RANGE_CHOP, confidence=0.3)

        # Score each regime
        scores = {r: 0.0 for r in ALL_REGIMES}

        scores[REGIME_TREND_EXPANSION] = self._score_trend_expansion(velocity, displacement)
        scores[REGIME_TREND_CONTINUATION] = self._score_trend_continuation(velocity, displacement)
        scores[REGIME_RANGE_CHOP] = self._score_range_chop(velocity, displacement)
        scores[REGIME_COMPRESSION] = self._score_compression(velocity)
        scores[REGIME_BREAKOUT] = self._score_breakout(velocity, displacement)
        scores[REGIME_EXHAUSTION] = self._score_exhaustion(velocity, displacement)
        scores[REGIME_REVERSAL] = self._score_reversal(velocity, displacement)

        # Smooth scores with exponential average
        self._regime_score_history.append(scores)
        smoothed = self._smooth_scores()

        # Pick winner
        winner = max(smoothed, key=smoothed.get)
        confidence = smoothed[winner]

        # Regime persistence (hysteresis: need 0.15 more to flip)
        if winner != self._current_regime:
            current_score = smoothed.get(self._current_regime, 0.0)
            if confidence - current_score < 0.15:
                winner = self._current_regime
                confidence = current_score

        # Track transitions
        if winner != self._current_regime:
            self._previous_regime = self._current_regime
            self._current_regime = winner
            self._regime_bars = 0
        self._regime_bars += 1

        # Velocity context
        vel_context = self._velocity_context(winner, velocity)

        # Transition probability
        trans_prob = self._transition_probability(smoothed, winner)

        # Trend direction from EMA alignment
        trend_dir, trend_str = self._trend_analysis()

        return RegimeState(
            regime=winner,
            confidence=round(confidence, 3),
            scores={k: round(v, 3) for k, v in smoothed.items()},
            velocity_context=vel_context,
            transition_probability=round(trans_prob, 3),
            previous_regime=self._previous_regime,
            bars_in_regime=self._regime_bars,
            trend_direction=trend_dir,
            trend_strength=round(trend_str, 3),
        )

    # ── Indicator updates ───────────────────────────────────────

    def _update_indicators(self, candle: LiveCandle) -> None:
        """Update EMAs, ATR, Bollinger width on candle close."""
        c = candle.close

        # EMAs
        if self._ema_20 is None:
            self._ema_20 = c
            self._ema_50 = c
        else:
            self._ema_20 = c * self._ema_k20 + self._ema_20 * (1 - self._ema_k20)
            self._ema_50 = c * self._ema_k50 + self._ema_50 * (1 - self._ema_k50)

        # ATR
        tr = candle.high - candle.low
        self._atr_values.append(tr)
        if len(self._atr_values) >= 14:
            self._atr_14 = sum(list(self._atr_values)[-14:]) / 14

        # Bollinger width (20-period stddev)
        if len(self._candles) >= 20:
            closes = [c.close for c in list(self._candles)[-20:]]
            mean = sum(closes) / len(closes)
            variance = sum((x - mean) ** 2 for x in closes) / len(closes)
            std = variance ** 0.5
            bb_width = (std * 2) / mean if mean > 0 else 0.0  # normalized width
            self._bb_width_history.append(bb_width)

    # ── Scoring functions ───────────────────────────────────────

    def _score_trend_expansion(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """Strong directional move: EMA alignment + velocity acceleration + high displacement."""
        score = 0.0

        # EMA alignment (20 above/below 50)
        if self._ema_20 and self._ema_50:
            ema_spread = abs(self._ema_20 - self._ema_50) / self._pip
            if ema_spread > 5.0:  # 5 pip separation
                score += 0.3

        # Velocity acceleration
        if vel.is_accelerating:
            score += 0.25

        # Displacement confirms direction
        if disp.classification == self.DISPLACEMENT_IMPULSE:
            score += 0.3

        # High tick efficiency
        if vel.tick_efficiency > 0.5:
            score += 0.15

        return min(score, 1.0)

    def _score_trend_continuation(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """Existing trend + moderate velocity."""
        score = 0.0

        # EMA alignment present but not extreme
        if self._ema_20 and self._ema_50:
            diff = (self._ema_20 - self._ema_50) / self._pip
            if 2.0 < abs(diff) < 15.0:
                score += 0.35

        # Moderate (not extreme) velocity
        if 30 < vel.percentile < 80:
            score += 0.25

        # Stable displacement ratio
        if 0.3 < disp.displacement_ratio < 0.7:
            score += 0.2

        # Not decaying
        if not vel.is_decaying:
            score += 0.2

        return min(score, 1.0)

    def _score_range_chop(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """No trend + low displacement."""
        score = 0.0

        # EMAs close together (no trend)
        if self._ema_20 and self._ema_50:
            diff = abs(self._ema_20 - self._ema_50) / self._pip
            if diff < 3.0:
                score += 0.3

        # Low displacement ratio
        if disp.displacement_ratio < 0.3:
            score += 0.25

        # Oscillating velocity (not extreme in either direction)
        if 20 < vel.percentile < 70:
            score += 0.2

        # Low tick efficiency
        if vel.tick_efficiency < 0.3:
            score += 0.25

        return min(score, 1.0)

    def _score_compression(self, vel: NormalizedVelocity) -> float:
        """Declining volatility + squeeze."""
        score = 0.0

        # Bollinger squeeze
        if len(self._bb_width_history) >= 10:
            recent_bw = list(self._bb_width_history)[-5:]
            older_bw = list(self._bb_width_history)[-10:-5]
            if older_bw and recent_bw:
                avg_recent = sum(recent_bw) / len(recent_bw)
                avg_older = sum(older_bw) / len(older_bw)
                if avg_older > 0 and avg_recent / avg_older < 0.7:
                    score += 0.35

        # Declining ATR
        if len(self._atr_values) >= 14:
            recent_atr = sum(list(self._atr_values)[-5:]) / 5
            older_atr = sum(list(self._atr_values)[-14:-5]) / 9
            if older_atr > 0 and recent_atr / older_atr < 0.75:
                score += 0.3

        # Low tick rate
        if vel.tick_rate_60s < 2.0:
            score += 0.2

        # Low velocity
        if vel.percentile < 25:
            score += 0.15

        return min(score, 1.0)

    def _score_breakout(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """Price beyond range + velocity spike + high displacement."""
        score = 0.0

        # Velocity spike from low base
        if vel.velocity_ratio > 3.0:
            score += 0.3

        # Unusual velocity
        if vel.is_unusual:
            score += 0.25

        # High displacement (genuine move)
        if disp.classification == self.DISPLACEMENT_IMPULSE:
            score += 0.3

        # Previous regime was compression (breakout FROM compression)
        if self._current_regime == REGIME_COMPRESSION:
            score += 0.15

        return min(score, 1.0)

    def _score_exhaustion(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """Velocity decaying, displacement divergence."""
        score = 0.0

        # Velocity is decaying
        if vel.is_decaying:
            score += 0.35

        # Displacement confirms exhaustion
        if disp.classification == self.DISPLACEMENT_EXHAUSTION:
            score += 0.3

        # Low tick efficiency (lots of movement, no progress)
        if vel.tick_efficiency < 0.15:
            score += 0.2

        # Was previously in expansion or breakout
        if self._current_regime in (REGIME_TREND_EXPANSION, REGIME_BREAKOUT):
            score += 0.15

        return min(score, 1.0)

    def _score_reversal(self, vel: NormalizedVelocity, disp: "DisplacementState") -> float:
        """Multi-factor reversal (scored conservatively)."""
        score = 0.0

        # Trap confirmed (high vel + low displacement = someone is absorbing)
        if disp.classification == self.DISPLACEMENT_TRAP:
            score += 0.25

        # Velocity decaying + displacement shifting direction
        if vel.is_decaying and disp.displacement_ratio < 0.2:
            score += 0.25

        # Volume imbalance flipping (buyers becoming sellers or vice versa)
        if abs(disp.volume_imbalance) > 0.6:
            score += 0.2

        # Previous regime was exhaustion (exhaustion → reversal is natural flow)
        if self._current_regime == REGIME_EXHAUSTION:
            score += 0.3

        return min(score, 1.0)

    # ── Helpers ──────────────────────────────────────────────────

    def _smooth_scores(self) -> Dict[str, float]:
        """Exponential average over recent score snapshots for noise reduction."""
        if not self._regime_score_history:
            return {r: 0.0 for r in ALL_REGIMES}

        history = list(self._regime_score_history)
        result = {r: 0.0 for r in ALL_REGIMES}
        weight_sum = 0.0

        for i, scores in enumerate(history):
            w = 2.0 ** i  # exponential weight (most recent = highest)
            weight_sum += w
            for r in ALL_REGIMES:
                result[r] += scores.get(r, 0.0) * w

        return {r: v / weight_sum for r, v in result.items()}

    def _velocity_context(self, regime: str, vel: NormalizedVelocity) -> str:
        """What does velocity mean in this specific regime?"""
        z = vel.z_score
        contexts = {
            REGIME_TREND_EXPANSION: "sustaining impulse" if z > 1 else "momentum waning",
            REGIME_TREND_CONTINUATION: "trend healthy" if z > 0 else "trend weakening",
            REGIME_RANGE_CHOP: "noise / chop" if z < 1.5 else "potential breakout building",
            REGIME_COMPRESSION: "normal for compression" if z < 1 else "BREAKOUT IMMINENT",
            REGIME_BREAKOUT: "confirming breakout" if z > 2 else "breakout weakening",
            REGIME_EXHAUSTION: "move exhausting" if vel.is_decaying else "still has momentum",
            REGIME_REVERSAL: "reversal velocity" if z > 1 else "weak reversal",
        }
        return contexts.get(regime, "unknown")

    def _transition_probability(self, scores: Dict[str, float], current: str) -> float:
        """Probability that the regime will change on the next bar."""
        if not scores:
            return 0.0
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) < 2:
            return 0.0
        # If #2 is close to #1, transition is likely
        gap = sorted_scores[0] - sorted_scores[1]
        return max(0.0, 1.0 - gap * 5.0)  # smaller gap = higher probability

    def _trend_analysis(self) -> tuple[str, float]:
        """Derive trend direction and strength from EMA alignment."""
        if self._ema_20 is None or self._ema_50 is None:
            return "sideways", 0.0

        diff = (self._ema_20 - self._ema_50) / self._pip
        if diff > 3.0:
            return "up", min(abs(diff) / 15.0, 1.0)
        elif diff < -3.0:
            return "down", min(abs(diff) / 15.0, 1.0)
        return "sideways", 0.0


__all__ = [
    "RegimeEngine",
    "RegimeState",
    "REGIME_TREND_EXPANSION",
    "REGIME_TREND_CONTINUATION",
    "REGIME_RANGE_CHOP",
    "REGIME_COMPRESSION",
    "REGIME_BREAKOUT",
    "REGIME_EXHAUSTION",
    "REGIME_REVERSAL",
    "ALL_REGIMES",
]
