"""Dynamic Velocity Threshold Engine for Adaptive Entry Detection.

Replaces static thresholds (percentile > 90, z > 2.0) with dynamic values
that adapt to current market regime: compression, expansion, trending.

Key insight: What counts as "unusual velocity" depends on market context:
- Compression: Lower threshold (50th percentile) - any spike is unusual
- Expansion: Higher threshold (85th percentile) - need strong move
- Trending: Normal threshold (75th percentile)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState


@dataclass
class DynamicVelocityThresholds:
    """Output: dynamic velocity thresholds based on market regime."""

    percentile_threshold: float  # Replaces static 90.0
    z_score_threshold: float  # Replaces static 2.0
    regime_name: str  # Current regime driving decision
    regime_factor: float  # Multiplier applied to base threshold
    reasoning: str  # Why this threshold


class VelocityThresholdEngine:
    """Computes dynamic velocity thresholds from market regime.

    Adapts percentile/z-score thresholds to:
    1. Market regime (compression vs expansion vs trending)
    2. Regime confidence (how locked-in is this regime?)
    3. Market volatility (high vol needs higher thresholds)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: Optional config dict with:
              - velocity_percentile_threshold (default 90.0)
              - velocity_z_score_threshold (default 2.0)
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

        # Store regime constants for use in other methods
        self.REGIME_COMPRESSION = REGIME_COMPRESSION
        self.REGIME_BREAKOUT = REGIME_BREAKOUT
        self.REGIME_TREND_EXPANSION = REGIME_TREND_EXPANSION
        self.REGIME_TREND_CONTINUATION = REGIME_TREND_CONTINUATION
        self.REGIME_EXHAUSTION = REGIME_EXHAUSTION
        self.REGIME_RANGE_CHOP = REGIME_RANGE_CHOP

        self.config = config or {}

        # Base thresholds
        self._base_percentile = self.config.get("velocity_percentile_threshold", 90.0)
        self._base_z_score = self.config.get("velocity_z_score_threshold", 2.0)

        # Regime-specific multipliers for PERCENTILE THRESHOLD (lower = more sensitive)
        self._percentile_factors = {
            REGIME_COMPRESSION: 0.35,       # VERY LOW (31st pct) - ANY tick above baseline is unusual
            REGIME_BREAKOUT: 1.1,           # Higher (99th pct) - filter false breakouts
            REGIME_TREND_EXPANSION: 0.70,   # Moderately lower (63th pct) - catch strong moves
            REGIME_TREND_CONTINUATION: 0.85, # Lower (76th pct)
            REGIME_EXHAUSTION: 0.60,        # Lower (54th pct) - exits imminent
            REGIME_RANGE_CHOP: 0.40,        # VERY LOW (36th pct) - choppy/sparse market! Any spike matters
        }

        # Regime-specific multipliers for Z-SCORE THRESHOLD (higher = less sensitive)
        self._z_score_factors = {
            REGIME_COMPRESSION: 0.60,       # LOWER (1.2) - any spike is unusual in quiet
            REGIME_BREAKOUT: 1.3,           # Higher (2.6) - filter false breakouts
            REGIME_TREND_EXPANSION: 0.95,   # Slightly lower (1.9) - catch strong moves
            REGIME_TREND_CONTINUATION: 1.0, # Normal (2.0)
            REGIME_EXHAUSTION: 0.80,        # Lower (1.6) - exits imminent
            REGIME_RANGE_CHOP: 0.70,        # LOWER (1.4) - choppy/quiet = need to catch any activity
        }

    def compute(
        self,
        regime: Optional["RegimeState"] = None,
        regime_confidence: float = 0.5,
        time_in_regime_seconds: int = 0,
    ) -> DynamicVelocityThresholds:
        """
        Compute dynamic velocity thresholds from regime.

        Args:
            regime: Current market regime (compression, expansion, etc.)
            regime_confidence: How confident are we in this regime? (0-1)
            time_in_regime_seconds: How long in this regime?

        Returns:
            DynamicVelocityThresholds with adapted percentile/z-score thresholds
        """
        # Default regime if none provided
        regime_name = regime.regime if regime else self.REGIME_RANGE_CHOP
        confidence = regime.confidence if regime else regime_confidence

        # Get regime multipliers
        pct_factor = self._percentile_factors.get(regime_name, 1.0)
        z_factor = self._z_score_factors.get(regime_name, 1.0)

        # Adjust for confidence (low confidence → closer to base thresholds)
        pct_factor = 1.0 + (pct_factor - 1.0) * confidence
        z_factor = 1.0 + (z_factor - 1.0) * confidence

        # Time-in-regime factor (longer in regime = more confident adjustment)
        time_factor = min(1.0, 1.0 + (time_in_regime_seconds / 600.0))  # Ramp up to 1.0 over 10 min
        pct_factor *= time_factor
        z_factor *= time_factor

        # Compute final thresholds
        percentile_threshold = round(self._base_percentile * pct_factor, 1)
        z_score_threshold = round(self._base_z_score * z_factor, 2)

        # Clamp to reasonable ranges
        percentile_threshold = max(50.0, min(99.0, percentile_threshold))
        z_score_threshold = max(1.0, min(4.0, z_score_threshold))

        # Reasoning
        if regime_name == self.REGIME_COMPRESSION:
            reasoning = f"Compression: Lower thresholds ({percentile_threshold:.0f}pct, z>{z_score_threshold:.1f}) - any spike is unusual"
        elif regime_name == self.REGIME_BREAKOUT:
            reasoning = f"Breakout: Higher thresholds ({percentile_threshold:.0f}pct, z>{z_score_threshold:.1f}) - filter false breakouts"
        elif regime_name == self.REGIME_TREND_EXPANSION:
            reasoning = f"Trend: Moderate thresholds ({percentile_threshold:.0f}pct, z>{z_score_threshold:.1f}) - catch strong moves"
        else:
            reasoning = f"Regime {regime_name}: Normal thresholds ({percentile_threshold:.0f}pct, z>{z_score_threshold:.1f})"

        return DynamicVelocityThresholds(
            percentile_threshold=percentile_threshold,
            z_score_threshold=z_score_threshold,
            regime_name=regime_name,
            regime_factor=pct_factor,
            reasoning=reasoning,
        )


__all__ = ["VelocityThresholdEngine", "DynamicVelocityThresholds"]
