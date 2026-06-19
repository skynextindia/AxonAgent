"""Liquidity Pool Engine.

Upgrades the legacy LevelBehaviorTracker. Instead of just tracking
bounces, this engine scores the probability that a level has been
"swept" (liquidity grabbed) vs "broken" (price accepting new area).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState, DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION
from axonai.dataflows.evidence_extractor import PriceLevel


@dataclass
class LiquidityEvent:
    """Represents a specific interaction with a price level."""
    timestamp: float
    level_price: float
    level_type: str
    interaction_type: str  # "APPROACH", "BREACH", "REJECTION", "SWEEP", "ACCEPTANCE"
    velocity_z: float
    displacement_ratio: float
    depth_pips: float      # How far past the level price went


@dataclass
class LevelState:
    """Live state of a single price level."""
    price: float
    level_type: str
    strength_score: float = 0.5
    
    # ── Interaction tracking ────────────────────────────────────
    touches: int = 0
    is_currently_breached: bool = False
    max_breach_depth_pips: float = 0.0
    time_since_breach_sec: float = 0.0
    
    # ── Liquidity mechanics ─────────────────────────────────────
    sweep_probability: float = 0.0     # 0.0 to 1.0
    acceptance_probability: float = 0.0 # 0.0 to 1.0
    is_swept: bool = False             # Confirmed liquidity grab
    is_broken: bool = False            # Confirmed structural break
    
    # History
    recent_events: List[LiquidityEvent] = field(default_factory=list)


@dataclass
class LiquidityState:
    """Output snapshot of the Liquidity Engine."""
    active_sweeps: List[LevelState] = field(default_factory=list)
    active_breaks: List[LevelState] = field(default_factory=list)
    nearest_support: Optional[LevelState] = None
    nearest_resistance: Optional[LevelState] = None
    
    # Global liquidity context
    liquidity_void_active: bool = False # Fast move with no nearby levels
    distance_to_nearest_level: float = 0.0


class LiquidityEngine:
    """Tracks how price interacts with structural liquidity pools."""

    def __init__(self, pip_mult: float = 0.0001, proximity_pips: float = 5.0):
        self._pip = pip_mult
        self._proximity = proximity_pips
        
        self._levels: Dict[float, LevelState] = {}
        self._last_price: float = 0.0
        self._last_time: float = 0.0

    def sync_levels(self, price_levels: List[PriceLevel]) -> None:
        """Sync the engine's internal state with the globally detected levels.
        Maintains tracking state for existing levels.
        """
        active_prices = set()
        for pl in price_levels:
            if not pl.is_active:
                continue
                
            active_prices.add(pl.price)
            if pl.price not in self._levels:
                self._levels[pl.price] = LevelState(
                    price=pl.price,
                    level_type=pl.level_type,
                    strength_score=pl.strength,
                    touches=pl.touches
                )
            else:
                # Update properties but keep interaction history
                self._levels[pl.price].strength_score = pl.strength
                self._levels[pl.price].level_type = pl.level_type
                
        # Prune dead levels
        for p in list(self._levels.keys()):
            if p not in active_prices:
                del self._levels[p]

    def update(
        self,
        price: float,
        timestamp: datetime,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
    ) -> LiquidityState:
        """Process one tick against all active levels."""
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
        dt = ts - self._last_time if self._last_time > 0 else 0.0
        
        active_sweeps = []
        active_breaks = []
        
        # Sort levels to find nearest
        sorted_levels = sorted(self._levels.values(), key=lambda x: x.price)
        support = None
        resistance = None
        
        for ls in sorted_levels:
            if ls.price < price:
                support = ls
            elif ls.price > price and resistance is None:
                resistance = ls
                
            dist_pips = (price - ls.price) / self._pip
            abs_dist = abs(dist_pips)
            
            # Update breach status
            was_breached = ls.is_currently_breached
            
            # Determine if currently breached
            # A level is breached if price crosses it. 
            # Support: price < level (dist < 0)
            # Resistance: price > level (dist > 0)
            # For simplicity, we just say it's breached if price is within 2 pips
            # on the "wrong" side, or deeply crossed.
            # Without explicit level direction, we infer:
            # If price was above it, it's support. If it goes below, it's breached.
            # To be robust, we just track the depth.
            
            if abs_dist < self._proximity:
                # Interaction occurring
                if not was_breached and abs_dist <= 1.0:
                    ls.is_currently_breached = True
                    ls.touches += 1
                    ls.recent_events.append(LiquidityEvent(
                        timestamp=ts,
                        level_price=ls.price,
                        level_type=ls.level_type,
                        interaction_type="BREACH",
                        velocity_z=velocity.z_score,
                        displacement_ratio=displacement.displacement_ratio,
                        depth_pips=abs_dist
                    ))
            
            if ls.is_currently_breached:
                ls.time_since_breach_sec += dt
                ls.max_breach_depth_pips = max(ls.max_breach_depth_pips, abs_dist)
                
                # Evaluate Sweep vs Break
                self._evaluate_breach(ls, price, velocity, displacement, ts)
                
                # Recovery - price moved away from breach
                if abs_dist > self._proximity and ls.time_since_breach_sec > 10.0:
                    ls.is_currently_breached = False
                    ls.time_since_breach_sec = 0.0
                    
            if ls.is_swept:
                active_sweeps.append(ls)
            if ls.is_broken:
                active_breaks.append(ls)

        # Global context
        nearest_dist = float('inf')
        if support:
            nearest_dist = min(nearest_dist, (price - support.price) / self._pip)
        if resistance:
            nearest_dist = min(nearest_dist, (resistance.price - price) / self._pip)
            
        is_void = nearest_dist > 25.0 and velocity.percentile > 80.0

        self._last_price = price
        self._last_time = ts

        return LiquidityState(
            active_sweeps=active_sweeps,
            active_breaks=active_breaks,
            nearest_support=support,
            nearest_resistance=resistance,
            liquidity_void_active=is_void,
            distance_to_nearest_level=round(nearest_dist, 1) if nearest_dist != float('inf') else 0.0
        )

    def _evaluate_breach(
        self,
        ls: LevelState,
        price: float,
        vel: NormalizedVelocity,
        disp: DisplacementState,
        ts: float
    ) -> None:
        """Determine if an active breach is a sweep or a structural break.
        
        SWEEP criteria:
        - Price breaches level
        - High velocity but TRAP/ABSORPTION displacement
        - Price snaps back relatively quickly
        
        BREAK criteria:
        - Price breaches level
        - IMPULSE displacement holds
        - Price spends time beyond the level without trapping
        """
        # If it's already decided recently, don't flip flop instantly
        if ls.is_swept or ls.is_broken:
            # Decay the states if price moves far away
            if ls.max_breach_depth_pips > 15.0 and not ls.is_currently_breached:
                ls.is_swept = False
                ls.is_broken = False
            return

        # SWEEP SCORING
        sweep_score = 0.0
        if disp.classification in (DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION):
            sweep_score += 0.4
        if vel.is_decaying:
            sweep_score += 0.3
        if ls.max_breach_depth_pips < 10.0 and ls.time_since_breach_sec > 5.0:
            sweep_score += 0.2
            
        # BREAK SCORING
        break_score = 0.0
        if disp.classification == DISPLACEMENT_IMPULSE:
            break_score += 0.5
        if ls.max_breach_depth_pips > 8.0:
            break_score += 0.3
        if ls.time_since_breach_sec > 60.0:
            break_score += 0.2

        ls.sweep_probability = min(sweep_score, 1.0)
        ls.acceptance_probability = min(break_score, 1.0)
        
        # Thresholds
        if ls.sweep_probability >= 0.7:
            ls.is_swept = True
            ls.recent_events.append(LiquidityEvent(
                timestamp=ts, level_price=ls.price, level_type=ls.level_type,
                interaction_type="SWEEP", velocity_z=vel.z_score,
                displacement_ratio=disp.displacement_ratio, depth_pips=ls.max_breach_depth_pips
            ))
            
        elif ls.acceptance_probability >= 0.8:
            ls.is_broken = True
            ls.recent_events.append(LiquidityEvent(
                timestamp=ts, level_price=ls.price, level_type=ls.level_type,
                interaction_type="ACCEPTANCE", velocity_z=vel.z_score,
                displacement_ratio=disp.displacement_ratio, depth_pips=ls.max_breach_depth_pips
            ))

__all__ = ["LiquidityEngine", "LiquidityState", "LevelState", "LiquidityEvent"]
