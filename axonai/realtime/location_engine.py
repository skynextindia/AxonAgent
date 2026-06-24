"""Location Engine: Market Structural Positioning.

Computes distance to key liquidity and structural levels.
Feeds into phase transition thresholds and retest trap detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LocationContext:
    """Market location metrics relative to key structural levels."""

    distance_to_liquidity: float         # ATR units to nearest liquidity cluster
    distance_to_sr: float                # ATR units to nearest S/R level
    room_available: float                # Pips from current price to next level
    at_structure: bool                   # Within config threshold of a level?
    nearest_level_type: str              # "support" / "resistance" / "none"
    nearest_level_price: float           # Price of the nearest level

    @property
    def is_safe_entry(self) -> bool:
        """True if we have at least 1 ATR of room before next level."""
        return self.distance_to_sr > 1.0 and not self.at_structure


class LocationEngine:
    """Computes market location (distance to levels, at_structure flag)."""

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        """
        Args:
            pip_mult: 0.0001 for majors, 0.01 for JPY pairs
            config: access to AT_STRUCTURE_ATR_THRESHOLD and other tunable params
        """
        self._pip = pip_mult
        self.config = config or {}

    def compute(
        self,
        price: float,
        atr_14_h1: float,
        recent_candles: list,  # List[LiveCandle] — not imported to avoid circular dep
        price_levels: list,    # List[PriceLevel] — active S/R levels from live_evidence
    ) -> LocationContext:
        """
        Compute market location metrics.

        Args:
            price: Current bid/ask midpoint
            atr_14_h1: H1 ATR (for distance calculation in ATR units)
            recent_candles: Last 50 M1 candles (for micro-resistance detection)
            price_levels: Active price levels from live_evidence (is_active=True only)

        Returns:
            LocationContext with distance metrics and at_structure flag

        Latency: <2ms per tick
        """
        at_struct_threshold = self.config.get("at_structure_atr_threshold", 0.5)

        if atr_14_h1 <= 0.0:
            atr_14_h1 = price * 0.0015

        # Find nearest active level above and below current price
        levels_below = [lv for lv in price_levels if lv.price < price and lv.is_active]
        levels_above = [lv for lv in price_levels if lv.price > price and lv.is_active]

        nearest_below = max(levels_below, key=lambda lv: lv.price) if levels_below else None
        nearest_above = min(levels_above, key=lambda lv: lv.price) if levels_above else None

        # Compute distance to each
        dist_to_below = (price - nearest_below.price) / atr_14_h1 if nearest_below else float("inf")
        dist_to_above = (nearest_above.price - price) / atr_14_h1 if nearest_above else float("inf")

        # Nearest level overall
        if dist_to_below <= dist_to_above:
            nearest_level = nearest_below
            nearest_distance = dist_to_below
            nearest_type = "support"
        else:
            nearest_level = nearest_above
            nearest_distance = dist_to_above
            nearest_type = "resistance"

        # If no levels exist, default
        if nearest_level is None:
            nearest_type = "none"
            nearest_distance = float("inf")
            nearest_level_price = price

            # Fall back to micro-resistance in recent candles (local highs/lows)
            if len(recent_candles) >= 10:
                recent_highs = [c.high for c in recent_candles[-10:]]
                recent_lows = [c.low for c in recent_candles[-10:]]
                max_high = max(recent_highs)
                min_low = min(recent_lows)

                dist_to_high = abs(price - max_high) / atr_14_h1
                dist_to_low = abs(price - min_low) / atr_14_h1

                if dist_to_high < dist_to_low and dist_to_high < nearest_distance:
                    nearest_distance = dist_to_high
                    nearest_level_price = max_high
                    nearest_type = "resistance"
                elif dist_to_low < nearest_distance:
                    nearest_distance = dist_to_low
                    nearest_level_price = min_low
                    nearest_type = "support"
                else:
                    nearest_level_price = price
            else:
                nearest_level_price = price
        else:
            nearest_level_price = nearest_level.price

        # Room available (pips to next level)
        if nearest_above:
            room_pips = (nearest_above.price - price) / self._pip
        elif nearest_below:
            room_pips = (price - nearest_below.price) / self._pip
        else:
            room_pips = 10.0  # Safe default if no levels

        # at_structure flag
        at_struct = nearest_distance <= at_struct_threshold

        # distance_to_liquidity: approximate as distance_to_sr
        # (In a fuller implementation, would scan order flow; here we use S/R as proxy)
        distance_to_liquidity = nearest_distance

        return LocationContext(
            distance_to_liquidity=round(distance_to_liquidity, 2),
            distance_to_sr=round(nearest_distance, 2),
            room_available=round(room_pips, 1),
            at_structure=at_struct,
            nearest_level_type=nearest_type,
            nearest_level_price=round(nearest_level_price, 5),
        )


if __name__ == "__main__":
    # Smoke test
    from datetime import datetime

    # Fake PriceLevel class for testing
    class FakeLevel:
        def __init__(self, price, is_active=True):
            self.price = price
            self.is_active = is_active

    # Fake LiveCandle class for testing
    class FakeCandle:
        def __init__(self, high, low):
            self.high = high
            self.low = low

    engine = LocationEngine(pip_mult=0.0001, config={"at_structure_atr_threshold": 0.5})

    # Test 1: Price near support level
    levels = [FakeLevel(1.0800), FakeLevel(1.0900), FakeLevel(1.0700)]
    candles = [FakeCandle(1.0810, 1.0790) for _ in range(10)]

    context = engine.compute(price=1.0805, atr_14_h1=0.0050, recent_candles=candles, price_levels=levels)

    test1_pass = (
        context.at_structure == True
        and context.nearest_level_type == "support"
        and context.nearest_level_price == 1.0800
    )
    print(f"  Test 1 (at_structure=True near support): {'PASS' if test1_pass else 'FAIL'}")
    if not test1_pass:
        print(
            f"    Expected: at_structure=True, type=support, price=1.0800"
            f"    Got: at_structure={context.at_structure}, type={context.nearest_level_type}, price={context.nearest_level_price}"
        )

    # Test 2: Price in open space
    context = engine.compute(price=1.0840, atr_14_h1=0.0050, recent_candles=candles, price_levels=levels)

    test2_pass = context.at_structure == False and context.room_available > 5.0
    print(f"  Test 2 (open space, room available): {'PASS' if test2_pass else 'FAIL'}")
    if not test2_pass:
        print(f"    Expected: at_structure=False, room>5; Got: at_structure={context.at_structure}, room={context.room_available}")

    # Test 3: No levels (fallback to candles)
    context = engine.compute(price=1.0800, atr_14_h1=0.0050, recent_candles=candles, price_levels=[])

    test3_pass = context.nearest_level_type in ["support", "resistance"]
    print(f"  Test 3 (no levels, fallback to candles): {'PASS' if test3_pass else 'FAIL'}")
    if not test3_pass:
        print(f"    Expected: type in [support, resistance]; Got: type={context.nearest_level_type}")

    all_pass = test1_pass and test2_pass and test3_pass
    print(f"\nlocation_engine.py: 3/3 tests {'PASSED' if all_pass else 'FAILED'}")
    if not all_pass:
        exit(1)
