"""Tick-level behavior tracking around price levels.

Monitors how price interacts with each active PriceLevel in real-time:
- Attack sequences (consecutive approaches)
- Tick density / absorption (stalling without progress)
- Rejection velocity (sharpness of bounces)
- Order flow imbalance at level boundaries

All data is fed back into MarketEvidence (for LLM agents) and
EventDetector (for smarter event gating).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LevelBehavior:
    """Per-level real-time tick behavior tracking state."""

    # Identity
    price: float
    level_type: str
    strength: float

    # ---- Attack sequence ----
    total_attacks: int = 0
    consecutive_attacks: int = 0
    last_attack_time: Optional[datetime] = None

    # ---- Current approach state ----
    in_approach: bool = False
    approach_start_time: Optional[datetime] = None
    approach_peak_price: Optional[float] = None
    ticks_in_zone: int = 0
    buy_volume_zone: float = 0.0
    sell_volume_zone: float = 0.0
    zone_entry_price: Optional[float] = None

    # ---- Rejection history ----
    rejection_count: int = 0
    last_rejection_velocity: float = 0.0
    rejection_velocities: deque = field(
        default_factory=lambda: deque(maxlen=10)
    )

    # ---- Last completed approach summary ----
    last_absorption_ratio: float = 0.0
    last_imbalance: float = 0.0
    last_approach_duration_ms: int = 0

    # ---- Inter-attack consolidation ----
    has_pullback: bool = False
    max_pullback_distance: float = 0.0


class LevelBehaviorTracker:
    """Tracks tick-level interactions with all active PriceLevels.

    Call ``update(mid, bid, ask, timestamp, volume, active_levels)`` on every tick.
    Call ``get_behavior_summary()`` to retrieve current state for MarketEvidence.
    """

    def __init__(
        self,
        pip_mult: float = 0.0001,
        outer_zone_pips: float = 5.0,
        inner_zone_pips: float = 2.0,
        rejection_confirm_pips: float = 5.0,
        pullback_reset_pips: float = 10.0,
        max_approach_duration_sec: float = 120.0,
        absorption_ticks_threshold: int = 30,
    ):
        self._pip_mult = pip_mult
        self._outer_zone = outer_zone_pips * pip_mult
        self._inner_zone = inner_zone_pips * pip_mult
        self._rejection_confirm = rejection_confirm_pips * pip_mult
        self._pullback_reset = pullback_reset_pips * pip_mult
        self._max_approach_sec = max_approach_duration_sec
        self._absorption_ticks_threshold = absorption_ticks_threshold

        # State keyed by level price (float)
        self._behaviors: Dict[float, LevelBehavior] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def update(
        self,
        mid: float,
        bid: float,
        ask: float,
        timestamp: datetime,
        volume: float,
        active_levels: list,
    ) -> None:
        """Process one tick across all active levels. O(n) where n = levels."""
        if not active_levels:
            return

        # 1. Update existing behaviors for all tracked levels
        self._update_current_approaches(mid, timestamp)

        # 2. Process each active level
        for level in active_levels:
            price = level.price
            distance = abs(mid - price)
            distance_pips = distance / self._pip_mult

            # Get or create behavior tracker for this level
            bhv = self._behaviors.get(price)
            if bhv is None:
                bhv = LevelBehavior(
                    price=price,
                    level_type=getattr(level, "level_type", "UNKNOWN"),
                    strength=getattr(level, "strength", 0.0),
                )
                self._behaviors[price] = bhv

            if distance_pips > (self._outer_zone / self._pip_mult):
                # Price is away from level
                if bhv.in_approach:
                    self._finalize_approach(bhv, mid, timestamp)
                continue

            # Price is within outer zone
            if not bhv.in_approach:
                # Start a new approach
                self._start_approach(bhv, mid, timestamp)

            # Update approach peak (farthest price reached toward the level)
            direction = getattr(level, "direction", "")
            if bhv.approach_peak_price is not None:
                if "resistance" in direction and mid > bhv.approach_peak_price:
                    bhv.approach_peak_price = mid
                elif "support" in direction and mid < bhv.approach_peak_price:
                    bhv.approach_peak_price = mid

            bhv.ticks_in_zone += 1

            # Directional volume tracking
            if volume > 0 and bhv.zone_entry_price is not None:
                if mid > bhv.zone_entry_price:
                    bhv.buy_volume_zone += volume
                elif mid < bhv.zone_entry_price:
                    bhv.sell_volume_zone += volume

    def get_behavior_summary(self) -> Dict[str, Dict]:
        """Return current level behavior state for serialization into MarketEvidence.

        Returns dict keyed by level price string like "1.16500".
        Only includes levels that have been approached at least once.
        """
        summary = {}
        now = datetime.now()
        for price, bhv in self._behaviors.items():
            if bhv.total_attacks == 0:
                continue

            status = self._get_status(bhv, now)

            # Absorption ratio (ticks per pip of progress)
            absorption = 0.0
            if bhv.ticks_in_zone > 0 and bhv.approach_peak_price and bhv.zone_entry_price:
                progress = abs(bhv.approach_peak_price - bhv.zone_entry_price) / self._pip_mult
                if progress > 0:
                    absorption = bhv.ticks_in_zone / progress

            # Imbalance for current approach
            imbalance = 0.0
            total_vol = bhv.buy_volume_zone + bhv.sell_volume_zone
            if total_vol > 0:
                imbalance = (bhv.buy_volume_zone - bhv.sell_volume_zone) / total_vol

            # Average rejection velocity
            avg_rejection_vel = 0.0
            if bhv.rejection_velocities:
                avg_rejection_vel = (
                    sum(bhv.rejection_velocities) / len(bhv.rejection_velocities)
                )

            key = f"{price:.5f}"
            summary[key] = {
                "price": price,
                "type": bhv.level_type,
                "strength": bhv.strength,
                "status": status,
                "total_attacks": bhv.total_attacks,
                "consecutive_attacks": bhv.consecutive_attacks,
                "rejection_count": bhv.rejection_count,
                "ticks_in_zone": bhv.ticks_in_zone,
                "absorption_ratio": round(absorption, 1),
                "last_rejection_velocity": round(bhv.last_rejection_velocity, 2),
                "avg_rejection_velocity": round(avg_rejection_vel, 2),
                "imbalance": round(imbalance, 3),
                "approach_duration_ms": bhv.last_approach_duration_ms,
                "has_pullback": bhv.has_pullback,
                "is_absorbing": (
                    (absorption > self._absorption_ticks_threshold and bhv.ticks_in_zone > 30)
                    or (bhv.in_approach and bhv.ticks_in_zone > 30 and bhv.approach_peak_price and abs(bhv.approach_peak_price - bhv.zone_entry_price) / self._pip_mult < 0.5)
                ),
                "attack_quality": self._classify_attack_quality(bhv, absorption),
            }
        return summary

    def get_level_behavior(self, level_price: float) -> Optional[LevelBehavior]:
        """Direct access for EventDetector queries."""
        return self._behaviors.get(level_price)

    def is_absorbing(self, level_price: float) -> bool:
        """Quick check: is price absorbing at this level?"""
        bhv = self._behaviors.get(level_price)
        if not bhv or not bhv.in_approach or bhv.ticks_in_zone < 30:
            return False
        progress = 0.0
        if bhv.approach_peak_price and bhv.zone_entry_price:
            progress = (
                abs(bhv.approach_peak_price - bhv.zone_entry_price) / self._pip_mult
            )
        if progress <= 0:
            return True
        return (bhv.ticks_in_zone / progress) > self._absorption_ticks_threshold

    def get_attack_count(self, level_price: float) -> int:
        """How many times has this level been attacked?"""
        bhv = self._behaviors.get(level_price)
        return bhv.total_attacks if bhv else 0

    def get_consecutive_attacks(self, level_price: float) -> int:
        """How many consecutive approaches without full pullback?"""
        bhv = self._behaviors.get(level_price)
        return bhv.consecutive_attacks if bhv else 0

    def prune_old_behaviors(
        self, active_prices: set, max_age_seconds: float = 7200
    ) -> None:
        """Remove behaviors for levels no longer active or not seen for a while."""
        now = datetime.now()
        stale = []
        for price, bhv in self._behaviors.items():
            if price not in active_prices:
                stale.append(price)
            elif (
                bhv.last_attack_time
                and (now - bhv.last_attack_time).total_seconds() > max_age_seconds
            ):
                stale.append(price)
        for price in stale:
            del self._behaviors[price]

    def reset(self) -> None:
        """Clear all tracked behavior state."""
        self._behaviors.clear()

    # ── Internal helpers ────────────────────────────────────────────────

    def _start_approach(
        self, bhv: LevelBehavior, mid: float, timestamp: datetime
    ) -> None:
        """Initialize a new approach tracking cycle."""
        bhv.in_approach = True
        bhv.approach_start_time = timestamp
        bhv.zone_entry_price = mid
        bhv.approach_peak_price = mid
        bhv.ticks_in_zone = 0
        bhv.buy_volume_zone = 0.0
        bhv.sell_volume_zone = 0.0
        bhv.total_attacks += 1
        bhv.consecutive_attacks += 1
        bhv.last_attack_time = timestamp

    def _update_current_approaches(self, mid: float, timestamp: datetime) -> None:
        """Enforce max approach duration."""
        for bhv in self._behaviors.values():
            if not bhv.in_approach:
                continue
            if (
                bhv.approach_start_time
                and (timestamp - bhv.approach_start_time).total_seconds()
                > self._max_approach_sec
            ):
                self._finalize_approach(bhv, mid, timestamp, force_end=True)
                logger.debug(
                    "LevelTracker: force-closed approach at %.5f (timeout)", bhv.price
                )

    def _finalize_approach(
        self,
        bhv: LevelBehavior,
        current_mid: float,
        timestamp: datetime,
        force_end: bool = False,
    ) -> None:
        """Close an active approach and classify outcome."""
        if not bhv.in_approach:
            return

        bhv.in_approach = False

        # Duration
        if bhv.approach_start_time:
            duration = (timestamp - bhv.approach_start_time).total_seconds() * 1000
            bhv.last_approach_duration_ms = int(duration)

        # Absorption ratio
        absorption = 0.0
        if bhv.ticks_in_zone > 0 and bhv.approach_peak_price and bhv.zone_entry_price:
            progress = (
                abs(bhv.approach_peak_price - bhv.zone_entry_price) / self._pip_mult
            )
            if progress > 0:
                absorption = bhv.ticks_in_zone / progress
        bhv.last_absorption_ratio = absorption

        # Imbalance
        total_vol = bhv.buy_volume_zone + bhv.sell_volume_zone
        if total_vol > 0:
            bhv.last_imbalance = (
                (bhv.buy_volume_zone - bhv.sell_volume_zone) / total_vol
            )

        # --- Pullback tracking ---
        # After finalizing, record how far price pulled back from peak
        current_dist_from_level = abs(current_mid - bhv.price)
        if bhv.approach_peak_price:
            peak_dist_from_level = abs(bhv.approach_peak_price - bhv.price)
        else:
            peak_dist_from_level = 0.0

        # If price moved away from the level since the peak, that's a pullback
        pullback = max(0.0, peak_dist_from_level - current_dist_from_level)
        if pullback > self._pullback_reset * 0.5:  # half of reset threshold
            bhv.has_pullback = True
            bhv.max_pullback_distance = max(bhv.max_pullback_distance, pullback)

        # Classify outcome
        if bhv.approach_peak_price and bhv.zone_entry_price and not force_end:
            bounce = (
                abs(current_mid - bhv.approach_peak_price) / self._pip_mult
            )
            bounce_confirm = self._rejection_confirm / self._pip_mult
            inner_zone_dist = self._inner_zone / self._pip_mult
            current_dist = abs(current_mid - bhv.price) / self._pip_mult

            if bounce > bounce_confirm and current_dist > inner_zone_dist:
                # Confirmed rejection
                bhv.rejection_count += 1
                if bhv.approach_start_time:
                    time_to_recover = max(
                        0.001,
                        (timestamp - bhv.approach_start_time).total_seconds(),
                    )
                    bhv.last_rejection_velocity = bounce / time_to_recover
                    bhv.rejection_velocities.append(bhv.last_rejection_velocity)

        # If pullback was large enough, reset consecutive attacks
        if (
            pullback > self._pullback_reset
            and bhv.consecutive_attacks > 0
        ):
            bhv.consecutive_attacks = 0

    def _get_status(self, bhv: LevelBehavior, now: datetime) -> str:
        """Determine human-readable status for a level."""
        if bhv.in_approach:
            if bhv.ticks_in_zone > 20:
                return "probing"
            return "approaching"
        if bhv.last_attack_time:
            seconds_since = (now - bhv.last_attack_time).total_seconds()
            if seconds_since < 30:
                if bhv.last_rejection_velocity > 0:
                    return "rejected"
                return "breaking"
            return "away"
        return "away"

    def _classify_attack_quality(
        self, bhv: LevelBehavior, absorption: float
    ) -> str:
        """Summarize the quality of price interaction at this level."""
        if bhv.total_attacks == 0:
            return "untested"
        if bhv.rejection_count >= 3 and bhv.last_rejection_velocity > 5.0:
            return "strong_defense"
        if bhv.consecutive_attacks >= 3 and absorption > self._absorption_ticks_threshold:
            return "weakening"
        if bhv.consecutive_attacks >= 3:
            return "pressured"
        if bhv.rejection_count >= 2:
            return "tested"
        if bhv.total_attacks == 1 and bhv.last_rejection_velocity > 8.0:
            return "rejected_sharply"
        return "approached"
