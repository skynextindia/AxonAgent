"""Dynamic Market Buffer Engine for Adaptive Velocity Exit Trails.

Replaces static thresholds (0.20 aligned, 0.40 unaligned) with dynamic values
that adapt to current market conditions: regime, volatility, and time-in-trade.

Key insight: Market buffer (trail distance) should expand/contract based on
whether the market is compressed (tight exit) or expanding (loose exit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState


@dataclass
class DynamicBuffer:
    """Output: dynamic exit trail threshold for velocity decay."""

    threshold: float  # Base threshold (replaces static 0.20 / 0.40)
    compression_factor: float  # Tightens exit in compression regimes
    expansion_factor: float  # Loosens exit in expansion regimes
    time_factor: float  # Adjusts based on time-in-trade
    regime_name: str  # Current market regime driving the decision


class MarketBufferEngine:
    """Computes dynamic velocity exit trail thresholds from market conditions.

    Adapts the trail distance to:
    1. Market regime (compression → tight; expansion → loose)
    2. Volatility/displacement intensity (high activity → looser)
    3. Time in trade (longer → can tighten)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: Optional config dict with:
              - realtime_velocity_decay_threshold_aligned (default 0.20)
              - realtime_velocity_decay_threshold_unaligned (default 0.40)
        """
        # Late import to avoid circular dependency
        from axonai.realtime.regime_engine import (
            REGIME_COMPRESSION,
            REGIME_BREAKOUT,
            REGIME_TREND_EXPANSION,
            REGIME_EXHAUSTION,
        )

        # Store regime constants for use in other methods
        self.REGIME_COMPRESSION = REGIME_COMPRESSION
        self.REGIME_BREAKOUT = REGIME_BREAKOUT
        self.REGIME_TREND_EXPANSION = REGIME_TREND_EXPANSION
        self.REGIME_EXHAUSTION = REGIME_EXHAUSTION

        self.config = config or {}

        # Base thresholds (replaced by dynamic calculation)
        self._base_threshold_aligned = self.config.get("realtime_velocity_decay_threshold_aligned", 0.20)
        self._base_threshold_unaligned = self.config.get("realtime_velocity_decay_threshold_unaligned", 0.40)

        # Regime multipliers
        self._regime_factors = {
            REGIME_COMPRESSION: 0.5,        # Tight: 50% of base
            REGIME_BREAKOUT: 1.5,           # Loose: 150% of base
            REGIME_TREND_EXPANSION: 1.3,    # Moderately loose
            REGIME_EXHAUSTION: 0.8,         # Slightly tighter (exit imminent)
        }

    def compute(
        self,
        regime: "RegimeState",
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        ticks_in_trade: int,
        is_htf_aligned: bool = False,
    ) -> DynamicBuffer:
        """
        Compute dynamic buffer (exit trail threshold) from market state.

        Args:
            regime: Current market regime (compression, expansion, etc.)
            velocity: Normalized velocity with z-score
            displacement: Displacement metrics (ratio, classification)
            ticks_in_trade: How long trade has been open (in ticks)
            is_htf_aligned: True if higher timeframe aligned with trade

        Returns:
            DynamicBuffer with adapted threshold
        """

        # Step 1: Select base threshold
        base = self._base_threshold_aligned if is_htf_aligned else self._base_threshold_unaligned

        # Step 2: Apply regime factor
        regime_name = regime.regime if regime else "UNKNOWN"
        regime_factor = self._regime_factors.get(regime_name, 1.0)

        # Step 3: Compression factor (based on displacement ratio)
        # High displacement = strong move = can afford looser exit
        # Low displacement = choppy/trapped = need tighter exit
        compression_factor = 1.0
        if displacement:
            if displacement.displacement_ratio > 0.7:
                compression_factor = 1.2  # Loose (strong directional move)
            elif displacement.displacement_ratio < 0.3:
                compression_factor = 0.8  # Tight (weak/choppy move)

        # Step 4: Expansion factor (based on velocity)
        # High velocity percentile = strong move = can be looser
        # Low velocity = weak = need tighter
        expansion_factor = 1.0
        if velocity:
            vel_pct = velocity.percentile
            if vel_pct > 75:
                expansion_factor = 1.3  # Very strong velocity
            elif vel_pct > 50:
                expansion_factor = 1.1  # Above average
            elif vel_pct < 25:
                expansion_factor = 0.7  # Weak velocity

        # Step 5: Time factor (longer in trade = slightly tighter for protection)
        time_factor = 1.0
        if ticks_in_trade > 500:  # Been in trade a while
            time_factor = 0.9
        elif ticks_in_trade > 1000:  # Very long trade
            time_factor = 0.8

        # Combine all factors
        threshold = base * regime_factor * compression_factor * expansion_factor * time_factor

        return DynamicBuffer(
            threshold=round(max(0.05, threshold), 4),  # Floor at 0.05
            compression_factor=compression_factor,
            expansion_factor=expansion_factor,
            time_factor=time_factor,
            regime_name=regime_name,
        )


__all__ = ["MarketBufferEngine", "DynamicBuffer"]
