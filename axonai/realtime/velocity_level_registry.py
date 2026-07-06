import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class VelocityLevel:
    price: float
    timestamp: float
    spike_velocity: float
    spike_percentile: float
    event_type: str         # "climax" or "sweep"
    direction: str          # "BUY" (swept support / expect bounce) or "SELL" (swept resistance / expect bounce)
    tested_count: int = 0
    is_active: bool = True
    zone_width_pips: float = 3.0  # Dynamic via vol_pips

class VelocityLevelRegistry:
    """Intraday dynamic liquidity pools created by velocity spikes."""

    def __init__(self, pip_mult: float = 0.0001):
        self._pip = pip_mult
        self.levels: List[VelocityLevel] = []

    def register_level(
        self,
        price: float,
        timestamp: float,
        spike_velocity: float,
        spike_percentile: float,
        event_type: str,
        direction: str,
        vol_pips: float = 3.0
    ) -> VelocityLevel:
        """Register a new velocity event level or update an existing close level."""
        # Dynamic zone width matches vol_pips (minimum 2.0 pips to avoid overlap)
        zone_width = max(2.0, vol_pips)
        
        # Check if we already have a level near this price (within zone width)
        for lvl in self.levels:
            if lvl.is_active and lvl.direction == direction:
                dist = abs(price - lvl.price) / self._pip
                if dist <= zone_width:
                    # Update existing level
                    lvl.price = (lvl.price + price) / 2.0  # Average out the levels
                    lvl.tested_count += 1
                    logger.debug("VelocityLevelRegistry: Updated level near %.5f (new price: %.5f)", price, lvl.price)
                    return lvl

        # Otherwise create a new level
        new_lvl = VelocityLevel(
            price=price,
            timestamp=timestamp,
            spike_velocity=spike_velocity,
            spike_percentile=spike_percentile,
            event_type=event_type,
            direction=direction,
            tested_count=0,
            is_active=True,
            zone_width_pips=zone_width
        )
        self.levels.append(new_lvl)
        logger.info(
            "VelocityLevelRegistry: Registered new %s level at %.5f (zone=%.1f pips)",
            direction, price, zone_width
        )
        return new_lvl

    def check_retest(self, price: float, direction: str, vol_pips: float = 3.0) -> Optional[VelocityLevel]:
        """Check if current price is within the zone of an active level for a retest."""
        for lvl in self.levels:
            if lvl.is_active and lvl.direction == direction:
                dist = abs(price - lvl.price) / self._pip
                # Use current vol_pips dynamically if level's zone_width_pips is older
                zone_width = max(2.0, vol_pips)
                if dist <= zone_width:
                    return lvl
        return None

    def invalidate_broken_levels(self, price: float) -> None:
        """Invalidate levels that have been cleanly broken (beyond-extreme)."""
        for lvl in self.levels:
            if not lvl.is_active:
                continue
            
            # For a SELL level (resistance), if price breaks above it, it is invalidated.
            # For a BUY level (support), if price breaks below it, it is invalidated.
            dist = (price - lvl.price) / self._pip
            if lvl.direction == "SELL" and dist > lvl.zone_width_pips:
                lvl.is_active = False
                logger.info("VelocityLevelRegistry: Invalidated SELL level at %.5f (broken above by %.1f pips)", lvl.price, dist)
            elif lvl.direction == "BUY" and dist < -lvl.zone_width_pips:
                lvl.is_active = False
                logger.info("VelocityLevelRegistry: Invalidated BUY level at %.5f (broken below by %.1f pips)", lvl.price, -dist)

    def reset(self) -> None:
        """Clear the registry (call daily/on reset)."""
        self.levels.clear()
