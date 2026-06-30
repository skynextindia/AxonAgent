"""Trade State Engine: Lifecycle Phase Tracking.

Tracks trade lifecycle phases (ENTRY → EXPANSION → ... → EXIT).
Updates health score, MFE/MAE, and thesis status every tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Phase constants
PHASE_ENTRY = "ENTRY"
PHASE_EXPANSION = "EXPANSION"
PHASE_CONTINUATION = "CONTINUATION"
PHASE_COMPRESSION = "COMPRESSION"
PHASE_EXHAUSTION = "EXHAUSTION"
PHASE_EXIT = "EXIT"

PHASE_ORDER = [PHASE_ENTRY, PHASE_EXPANSION, PHASE_CONTINUATION, PHASE_COMPRESSION, PHASE_EXHAUSTION, PHASE_EXIT]


@dataclass
class TradeState:
    """Complete lifecycle state of an active trade. FIX 1: ticks_in_trade as field."""

    # Identity
    ticket: int  # MT5 position ticket
    direction: str  # "BUY" or "SELL"
    entry_price: float
    entry_time: datetime
    entry_reason: str
    position_size: float  # Lots

    # Entry Context (from snapshot at entry time)
    entry_regime: str  # Regime when entered
    entry_velocity_percentile: float  # Velocity at entry (0-100)
    entry_displacement_class: str  # Displacement at entry

    # Lifecycle Phase
    current_phase: str = PHASE_ENTRY
    bars_in_phase: int = 0  # Debounce counter
    phase_transition_log: Dict[str, datetime] = field(default_factory=dict)

    # FIX 1: Changed from broken property to actual field
    ticks_in_trade: int = 0  # Total tick count since entry

    # Health Metrics
    health_score: float = 100.0  # 0-100
    thesis_status: str = "CONFIRMED"  # CONFIRMED | WEAKENING | BROKEN

    # Tick-by-Tick Performance
    mfe: float = 0.0  # Max Favorable Excursion (pips)
    mae: float = 0.0  # Max Adverse Excursion (pips)
    current_profit_pips: float = 0.0

    # Latest Market Context (updated every tick)
    last_velocity_percentile: float = 0.0
    last_displacement: str = "NEUTRAL"
    last_velocity_direction_favorable: bool = True
    last_displacement_direction_favorable: bool = True

    # Structural Context
    location_context: Optional[Dict] = None
    htf_context: str = "NEUTRAL"

    # Tick Event Flags
    tick_gap_detected: bool = False

    # Exit State (when closed)
    close_reason: Optional[str] = None
    close_price: Optional[float] = None
    close_time: Optional[datetime] = None
    is_active: bool = True  # False once the trade has been closed/exited


class TradeStateEngine:
    """Stateful trade lifecycle manager."""

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        """
        Args:
            pip_mult: 0.0001 for majors, 0.01 for JPY
            config: access to phase transition thresholds
        """
        self._pip = pip_mult
        self.config = config or {}
        self._state: Optional[TradeState] = None
        self._entry_high: float = 0.0
        self._entry_low: float = 0.0

    def register_trade(
        self,
        ticket: int,
        direction: str,
        entry_price: float,
        entry_time: datetime,
        entry_sl: float,
        entry_tp: float,
        entry_reason: str,
        position_size: float = 0.01,
        entry_regime: str = "UNKNOWN",
        entry_velocity_percentile: float = 0.0,
        entry_displacement_class: str = "NEUTRAL",
    ) -> None:
        """Register a new trade with the state engine."""
        self._state = TradeState(
            ticket=ticket,
            direction=direction.upper(),
            entry_price=entry_price,
            entry_time=entry_time,
            entry_reason=entry_reason,
            position_size=position_size,
            entry_regime=entry_regime,
            entry_velocity_percentile=entry_velocity_percentile,
            entry_displacement_class=entry_displacement_class,
        )
        self._entry_high = entry_price
        self._entry_low = entry_price
        logger.info(
            "TradeStateEngine: Trade registered | ticket=%d | direction=%s | entry=%.5f | size=%.2f",
            ticket,
            direction,
            entry_price,
            position_size,
        )

    def on_tick(
        self,
        price: float,
        timestamp: datetime,
        snapshot,  # EngineSnapshot from reversal_model
        location_context,  # LocationContext from location_engine
        htf_context: str = "NEUTRAL",
    ) -> Optional[TradeState]:
        """
        Update trade state every tick.

        Args:
            price: Current price
            timestamp: Tick time
            snapshot: EngineSnapshot from reversal_model
            location_context: LocationContext from location_engine
            htf_context: "ALIGNED" / "NEUTRAL" / "OPPOSING"

        Returns:
            Updated TradeState if trade active, None otherwise
        """
        if not self._state or self._state.current_phase == PHASE_EXIT:
            return self._state

        # FIX 1: Increment ticks_in_trade at the top
        self._state.ticks_in_trade += 1
        self._state.bars_in_phase += 1

        # Update MFE/MAE
        if self._state.direction == "BUY":
            self._entry_high = max(self._entry_high, price)
            self._entry_low = min(self._entry_low, price)
            self._state.mfe = (self._entry_high - self._state.entry_price) / self._pip
            self._state.mae = (self._state.entry_price - self._entry_low) / self._pip
            self._state.current_profit_pips = (price - self._state.entry_price) / self._pip
        else:  # SELL
            self._entry_high = max(self._entry_high, price)
            self._entry_low = min(self._entry_low, price)
            self._state.mfe = (self._state.entry_price - self._entry_low) / self._pip
            self._state.mae = (self._entry_high - self._state.entry_price) / self._pip
            self._state.current_profit_pips = (self._state.entry_price - price) / self._pip

        # Extract velocity and displacement from snapshot
        vel_percentile = 0.0
        disp_class = "NEUTRAL"
        if snapshot is not None:
            vel_percentile = getattr(snapshot.velocity, "percentile", 0.0) if snapshot.velocity else 0.0
            disp_class = getattr(snapshot.displacement, "classification", "NEUTRAL") if snapshot.displacement else "NEUTRAL"

        self._state.last_velocity_percentile = vel_percentile
        self._state.last_displacement = disp_class

        # Determine if velocity/displacement are favorable
        if snapshot is not None:
            if snapshot.velocity:
                vel_is_unusual = getattr(snapshot.velocity, "is_unusual", False)
                self._state.last_velocity_direction_favorable = vel_is_unusual or vel_percentile > 50

            if snapshot.displacement:
                disp_pips = getattr(snapshot.displacement, "net_displacement_pips", 0.0)
                if self._state.direction == "BUY":
                    self._state.last_displacement_direction_favorable = disp_pips > 0
                else:
                    self._state.last_displacement_direction_favorable = disp_pips < 0

        # Update structural context
        if location_context:
            self._state.location_context = {
                "distance_to_sr": location_context.distance_to_sr,
                "distance_to_liquidity": location_context.distance_to_liquidity,
                "room_available": location_context.room_available,
                "at_structure": location_context.at_structure,
                "nearest_level_type": location_context.nearest_level_type,
                "nearest_level_price": location_context.nearest_level_price,
            }
        self._state.htf_context = htf_context

        # Phase transitions (simplified logic for smoke test)
        self._update_phase(price, snapshot, location_context)

        # Health score update
        self._update_health_score()

        return self._state

    def _update_phase(self, price: float, snapshot, location_context) -> None:
        """Update phase based on conditions. Min duration gate applied."""
        min_duration = self.config.get("trade_phase_min_duration_ticks", 3)

        if self._state.current_phase == PHASE_ENTRY:
            # Transition to EXPANSION when we have displacement in our direction
            if (
                self._state.bars_in_phase >= min_duration
                and self._state.last_displacement_direction_favorable
                and self._state.mfe > self.config.get("expansion_phase_min_displacement", 2.0)
            ):
                self._transition_phase(PHASE_EXPANSION)

        elif self._state.current_phase == PHASE_EXPANSION:
            # Stay in EXPANSION while favorable
            # Transition to COMPRESSION if losing momentum
            if (
                self._state.bars_in_phase >= min_duration
                and (
                    not self._state.last_velocity_direction_favorable
                    or (
                        location_context
                        and location_context.at_structure
                        and self._state.last_velocity_percentile < 30
                    )
                )
            ):
                self._transition_phase(PHASE_COMPRESSION)

        elif self._state.current_phase == PHASE_COMPRESSION:
            # In compression, waiting for next move
            # Transition to EXHAUSTION if high velocity but no net move
            if (
                self._state.bars_in_phase >= min_duration
                and self._state.last_velocity_percentile > 80
                and location_context
                and location_context.at_structure
            ):
                self._transition_phase(PHASE_EXHAUSTION)

        elif self._state.current_phase == PHASE_EXHAUSTION:
            # Time-gated exit from exhaustion (max 30 ticks)
            if self._state.bars_in_phase > 30:
                self._state.thesis_status = "BROKEN"

    def _transition_phase(self, new_phase: str) -> None:
        """Transition to a new phase."""
        old_phase = self._state.current_phase
        self._state.current_phase = new_phase
        self._state.bars_in_phase = 0
        self._state.phase_transition_log[new_phase] = datetime.now(timezone.utc) if hasattr(datetime, "UTC") else datetime.utcnow()
        logger.info(
            "TradeStateEngine: Phase transition %s -> %s | ticket=%d | mfe=%.1f pips",
            old_phase,
            new_phase,
            self._state.ticket,
            self._state.mfe,
        )

    def _update_health_score(self) -> None:
        """Compute health score from thesis + displacement + time + location."""
        thesis_health = 1.0 if self._state.thesis_status == "CONFIRMED" else (0.6 if self._state.thesis_status == "WEAKENING" else 0.2)

        disp_health = 1.0 if self._state.last_displacement_direction_favorable else 0.5

        time_health = 1.0 if self._state.ticks_in_trade < 60 else (0.8 if self._state.ticks_in_trade < 120 else 0.5)

        location_health = 0.4 if (self._state.location_context and self._state.location_context.get("at_structure", False)) else 1.0

        composite = (thesis_health * 0.4 + disp_health * 0.3 + time_health * 0.2 + location_health * 0.1) * 100.0

        self._state.health_score = max(0.0, min(100.0, composite))

    def is_position_live(self) -> bool:
        """Check if active trade is still registered."""
        return self._state is not None and self._state.current_phase != PHASE_EXIT

    def get_state(self) -> Optional[TradeState]:
        """Return current TradeState if trade is active."""
        if self._state and self._state.current_phase != PHASE_EXIT:
            return self._state
        return None

    def close_trade(self, close_reason: str, close_price: float) -> None:
        """Mark trade as closed."""
        if self._state:
            self._state.close_reason = close_reason
            self._state.close_price = close_price
            self._state.close_time = datetime.utcnow()
            self._state.current_phase = PHASE_EXIT
            logger.info(
                "TradeStateEngine: Trade closed | ticket=%d | reason=%s | profit=%.1f pips",
                self._state.ticket,
                close_reason,
                self._state.current_profit_pips,
            )

    def reset(self) -> None:
        """Clear all state."""
        self._state = None
        self._entry_high = 0.0
        self._entry_low = 0.0


if __name__ == "__main__":
    # Smoke test
    from datetime import datetime, timedelta, timezone

    engine = TradeStateEngine(pip_mult=0.0001, config={"trade_phase_min_duration_ticks": 3})

    # Register a trade
    entry_time = datetime.now(timezone.utc)
    engine.register_trade(
        ticket=12345,
        direction="BUY",
        entry_price=1.0800,
        entry_time=entry_time,
        entry_sl=1.0790,
        entry_tp=1.0820,
        entry_reason="Impulse breakout",
        position_size=0.01,
        entry_regime="TRENDING",
        entry_velocity_percentile=75.0,
        entry_displacement_class="IMPULSE",
    )

    # Create minimal snapshot for testing
    class FakeVelocity:
        percentile = 65.0
        is_unusual = True

    class FakeDisplacement:
        classification = "IMPULSE"
        net_displacement_pips = 5.0

    class FakeSnapshot:
        velocity = FakeVelocity()
        displacement = FakeDisplacement()

    # Create location context
    class FakeLocationContext:
        distance_to_sr = 2.0
        at_structure = False
        room_available = 50.0

    snapshot = FakeSnapshot()
    location_ctx = FakeLocationContext()

    # Simulate 10 ticks: price rising, should transition ENTRY -> EXPANSION after min 3 ticks
    test_pass = True
    for i in range(10):
        price = 1.0800 + (i * 0.0005)  # Price rising 5 pips per tick
        state = engine.on_tick(
            price=price,
            timestamp=entry_time + timedelta(seconds=i),
            snapshot=snapshot,
            location_context=location_ctx,
            htf_context="ALIGNED",
        )

        # Check phase progression
        # At tick 0,1 we're in ENTRY (bars_in_phase 1-2)
        # At tick 2+ (bars_in_phase >= 3), we transition to EXPANSION
        if i <= 1:
            if state.current_phase != PHASE_ENTRY:
                print(f"  [FAIL] Tick {i}: Should stay in ENTRY, got {state.current_phase}")
                test_pass = False
        else:
            if state.current_phase != PHASE_EXPANSION:
                print(f"  [FAIL] Tick {i}: Should be in EXPANSION (min 3 ticks reached at tick 2), got {state.current_phase}")
                test_pass = False

        # Check ticks_in_trade increments correctly (should match i+1)
        if state.ticks_in_trade != i + 1:
            print(f"  [FAIL] Tick {i}: ticks_in_trade should be {i+1}, got {state.ticks_in_trade}")
            test_pass = False

    # Final check
    final_state = engine.get_state()
    if final_state:
        print(f"  [OK] Final phase: {final_state.current_phase}, health: {final_state.health_score:.1f}, ticks: {final_state.ticks_in_trade}")
        print(f"  [OK] MFE: {final_state.mfe:.1f} pips, MAE: {final_state.mae:.1f} pips")
    else:
        print("  [FAIL] Trade should still be active")
        test_pass = False

    print(f"\ntrade_state_engine.py: {'PASSED' if test_pass else 'FAILED'}")
    if not test_pass:
        exit(1)
