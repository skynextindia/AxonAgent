"""Dynamic Displacement Buffer Engine for Adaptive Entry Thresholds.

Replaces static thresholds (0.60 impulse, 0.25 trap) with dynamic values
that adapt to current market regime: compression, expansion, trending.

Key insight: What counts as "impulse" depends on market context:
- Compression: Lower threshold (0.35) - rare moves are impulses
- Expansion: Higher threshold (0.65) - many moves happen normally
- Trending: Normal threshold (0.50-0.60)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState


@dataclass
class DynamicDisplacementThresholds:
    """Output: dynamic entry thresholds based on market regime."""

    impulse_threshold: float  # Replaces static 0.60
    trap_threshold: float  # Replaces static 0.25
    regime_name: str  # Current regime driving decision
    regime_factor: float  # Multiplier applied to base threshold
    reasoning: str  # Why this threshold


class DisplacementBufferEngine:
    """Computes dynamic displacement thresholds from market regime.

    Adapts impulse/trap thresholds to:
    1. Market regime (compression vs expansion vs trending)
    2. Regime confidence (how locked-in is this regime?)
    3. Time in regime (how long has this persisted?)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: Optional config dict with:
              - impulse_ratio_threshold (default 0.40)
              - trap_ratio_threshold (default 0.15)
        """
        # Late import to avoid circular dependency
        from axonai.realtime.regime_engine import (
            REGIME_COMPRESSION,
            REGIME_BREAKOUT,
            REGIME_TREND_EXPANSION,
            REGIME_TREND_CONTINUATION,
            REGIME_EXHAUSTION,
            REGIME_RANGE_CHOP,
        )

        self.config = config or {}

        # Store regime constants for use in other methods
        self.REGIME_COMPRESSION = REGIME_COMPRESSION
        self.REGIME_BREAKOUT = REGIME_BREAKOUT
        self.REGIME_TREND_EXPANSION = REGIME_TREND_EXPANSION
        self.REGIME_TREND_CONTINUATION = REGIME_TREND_CONTINUATION
        self.REGIME_EXHAUSTION = REGIME_EXHAUSTION
        self.REGIME_RANGE_CHOP = REGIME_RANGE_CHOP

        # Base thresholds (lowered for demo, will be replaced by dynamic)
        self._base_impulse = self.config.get("impulse_ratio_threshold", 0.40)
        self._base_trap = self.config.get("trap_ratio_threshold", 0.15)

        # Regime-specific multipliers
        self._regime_factors = {
            REGIME_COMPRESSION: {
                "impulse": 0.85,  # Lower (0.34) - rare moves matter
                "trap": 0.80,     # Lower (0.12) - market stuck
            },
            REGIME_BREAKOUT: {
                "impulse": 1.4,   # Higher (0.56) - filter noise
                "trap": 1.3,      # Higher (0.20) - many false breakouts
            },
            REGIME_TREND_EXPANSION: {
                "impulse": 1.2,   # Moderately higher (0.48)
                "trap": 1.1,      # Moderately higher (0.17)
            },
            REGIME_TREND_CONTINUATION: {
                "impulse": 1.0,   # Normal (0.40)
                "trap": 1.0,      # Normal (0.15)
            },
            REGIME_EXHAUSTION: {
                "impulse": 0.9,   # Slightly lower (0.36) - exits soon
                "trap": 0.9,      # Slightly lower (0.14)
            },
            REGIME_RANGE_CHOP: {
                "impulse": 1.1,   # Slightly higher (0.44) - choppy
                "trap": 1.2,      # Higher (0.18) - range-bound
            },
        }

    def compute(
        self,
        regime: Optional["RegimeState"] = None,
        regime_confidence: float = 0.5,
        time_in_regime_seconds: int = 0,
    ) -> DynamicDisplacementThresholds:
        """
        Compute dynamic displacement thresholds from regime.

        Args:
            regime: Current market regime (compression, expansion, etc.)
            regime_confidence: How confident are we in this regime? (0-1)
            time_in_regime_seconds: How long in this regime?

        Returns:
            DynamicDisplacementThresholds with adapted impulse/trap thresholds
        """
        # Default regime if none provided
        regime_name = regime.regime if regime else self.REGIME_RANGE_CHOP
        confidence = regime.confidence if regime else regime_confidence

        # Get regime multipliers
        factors = self._regime_factors.get(regime_name, {"impulse": 1.0, "trap": 1.0})

        # Adjust for confidence (low confidence → closer to base thresholds)
        impulse_factor = 1.0 + (factors["impulse"] - 1.0) * confidence
        trap_factor = 1.0 + (factors["trap"] - 1.0) * confidence

        # Time-in-regime factor (longer in regime = more confident adjustment)
        time_factor = min(1.0, 1.0 + (time_in_regime_seconds / 600.0))  # Ramp up to 1.0 over 10 min
        impulse_factor *= time_factor
        trap_factor *= time_factor

        # Compute final thresholds
        impulse_threshold = round(self._base_impulse * impulse_factor, 4)
        trap_threshold = round(self._base_trap * trap_factor, 4)

        # Clamp to reasonable ranges
        impulse_threshold = max(0.25, min(0.75, impulse_threshold))
        trap_threshold = max(0.05, min(0.35, trap_threshold))

        # Reasoning
        if regime_name == REGIME_COMPRESSION:
            reasoning = f"Compression: Lower thresholds ({impulse_threshold:.2f}) - rare moves are impulses"
        elif regime_name == REGIME_BREAKOUT:
            reasoning = f"Breakout: Higher thresholds ({impulse_threshold:.2f}) - filter false breakouts"
        elif regime_name == REGIME_TREND_EXPANSION:
            reasoning = f"Trend: Moderate thresholds ({impulse_threshold:.2f}) - strong directional move"
        else:
            reasoning = f"Regime {regime_name}: Normal thresholds ({impulse_threshold:.2f})"

        return DynamicDisplacementThresholds(
            impulse_threshold=impulse_threshold,
            trap_threshold=trap_threshold,
            regime_name=regime_name,
            regime_factor=impulse_factor,
            reasoning=reasoning,
        )


__all__ = ["DisplacementBufferEngine", "DynamicDisplacementThresholds"]
