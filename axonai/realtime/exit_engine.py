"""Exit Engine: Priority-Based Trade Exit Logic.

Evaluates exit conditions using a priority hierarchy:
1. Thesis Failure (highest urgency)
2. Adverse Impulse
3. Exhaustion Detection
4. Trailing Stop (legacy fallback, lowest urgency)

Wraps AdaptiveExitManager for safe transition period.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Decision to exit a trade or adjust stops."""

    should_exit: bool  # Time to close?
    action: str  # "HOLD" | "CLOSE_NOW" | "ADJUST_SL" | "ADJUST_TP"
    reason: str  # Human-readable reason
    urgency: float  # 0.0-1.0, how aggressive the exit

    suggested_sl: Optional[float] = None  # For ADJUST_SL action
    suggested_tp: Optional[float] = None  # For ADJUST_TP action

    @property
    def is_retest_trap(self) -> bool:
        """True if we detected a retest and are holding."""
        return "RETEST_TRAP" in self.reason and not self.should_exit


class ExitEngine:
    """Priority-based exit logic with legacy fallback."""

    def __init__(self, legacy_exit_manager, pip_mult: float = 0.0001, config: Optional[dict] = None):
        """
        Args:
            legacy_exit_manager: AdaptiveExitManager instance (fallback)
            pip_mult: 0.0001 for majors, 0.01 for JPY
            config: urgency multipliers and exit thresholds
        """
        self.legacy = legacy_exit_manager
        self._pip = pip_mult
        self.config = config or {}

    def evaluate(
        self,
        trade_state,  # TradeState from trade_state_engine
        snapshot,  # EngineSnapshot from reversal_model
        location_context,  # LocationContext from location_engine
        current_price: float,
    ) -> ExitSignal:
        """
        Evaluate exit conditions in priority order.

        Args:
            trade_state: Current trade state (phase, health, MFE, MAE)
            snapshot: EngineSnapshot (velocity, displacement, regime)
            location_context: LocationContext (distance to levels, at_structure)
            current_price: Current price

        Returns:
            ExitSignal with action and urgency

        Priority hierarchy:
        1. Thesis Failure (urgency 1.0)
        2. Adverse Impulse (urgency 0.9)
        3. Exhaustion (urgency 0.7)
        4. Trailing Stop (urgency 0.3, legacy fallback)
        """
        if not trade_state or not trade_state.is_active:
            return ExitSignal(should_exit=False, action="HOLD", reason="No active trade", urgency=0.0)

        # Extract metrics
        thesis_status = trade_state.thesis_status
        velocity_pct = trade_state.last_velocity_percentile
        displacement = trade_state.last_displacement
        at_structure = location_context.at_structure if location_context else False
        mfe = trade_state.mfe

        # Get HTF dampening multiplier
        htf_mult = 1.0
        if trade_state.htf_context == "OPPOSING":
            htf_mult = self.config.get("htf_opposing_sensitivity_multiplier", 1.5)
        elif trade_state.htf_context == "ALIGNED":
            htf_mult = self.config.get("htf_aligned_sensitivity_multiplier", 0.7)

        # Scale profit protection by live session volatility (vol_pips)
        vol_scale = 1.0
        if snapshot and hasattr(snapshot, "velocity") and snapshot.velocity:
            vp = getattr(snapshot.velocity, "vol_pips", None)
            if vp and vp > 0:
                vol_scale = max(0.5, min(vp / 1.0, 3.0))
        
        profit_protect_pips = float(self.config.get("exit_profit_protect_pips", 4.0)) * vol_scale

        # --- MINIMUM HOLD GATE ---
        # Measured 2026-07-23 over 109 real FX trades: median MFE offered was
        # 2.10p, but the market's own median favourable excursion is 2.80p at a
        # 15-minute hold, 5.50p at 60m and 11.32p at 240m, while round-trip cost
        # is a flat ~0.8-1.2p. The soft gates below were closing trades after
        # 2-4 minutes, i.e. before even the 15-minute opportunity had formed
        # (0 stop-loss and 0 take-profit hits in 109 trades — every close came
        # from a soft gate). Hold the position until it has had time to develop;
        # SL/TP still protect it throughout. 0.0 restores previous behaviour.
        elapsed_sec = 0.0
        entry_time = getattr(trade_state, "entry_time", None)
        if isinstance(entry_time, datetime):
            try:
                elapsed_sec = (datetime.now(entry_time.tzinfo) - entry_time).total_seconds()
            except (TypeError, ValueError, OverflowError):
                elapsed_sec = 0.0
        min_hold_sec = float(self.config.get("exit_min_hold_seconds", 0.0))
        soft_gates_armed = elapsed_sec >= min_hold_sec
        if not soft_gates_armed:
            return ExitSignal(
                should_exit=False,
                action="HOLD",
                reason=f"MIN_HOLD: {elapsed_sec:.0f}s of {min_hold_sec:.0f}s elapsed",
                urgency=0.0,
            )

        # --- PRIORITY 1: THESIS FAILURE (highest urgency) ---
        # Only close on thesis failure if the trade is NOT already meaningfully
        # profitable. Once in profit past exit_profit_protect_pips, hand it to
        # VelocityTrailingManager which will lock gains via SL — cutting here
        # would cap every winner at a scalp.
        if thesis_status == "BROKEN":
            if (
                velocity_pct > 50
                and displacement in ["ABSORPTION", "TRAP"]
                and trade_state.ticks_in_trade > self.config.get("trade_phase_min_duration_ticks", 3)
                and trade_state.current_profit_pips < profit_protect_pips
            ):
                urgency = self.config.get("thesis_failure_urgency", 1.0) * htf_mult
                return ExitSignal(
                    should_exit=True,
                    action="CLOSE_NOW",
                    reason=f"Thesis failure: displacement reversed ({displacement})",
                    urgency=min(1.0, urgency),
                )

        # --- PRIORITY 2: ADVERSE IMPULSE (high urgency) ---
        # Only cut on an adverse impulse if the trade is NOT already meaningfully
        # profitable and has been held past a minimum number of ticks. A single
        # opposing velocity spike is usually noise: cutting a winner on it caps
        # every winning trade at scalp size. Once the trade is in profit past
        # `exit_profit_protect_pips`, hand it to the trailing manager instead of
        # closing here. Losing/flat trades are still cut instantly (correct).
        # Gated by config. Measured 2026-07-23 across 109 real FX trades this is
        # the only net-negative exit gate: n=33, net -69.8p, expectancy -2.12p.
        # thesis_failure (+43.5p) and exhaustion (+4.9p) are both net positive.
        # Disabled deliberately on that evidence; set True to restore.
        adverse_enabled = self.config.get("exit_engine_enable_adverse_impulse", True)
        adverse_min_ticks = self.config.get("adverse_impulse_min_ticks", 30)

        # Check tick efficiency defensively from velocity snapshot
        tick_eff = 1.0
        if snapshot and hasattr(snapshot, "velocity") and snapshot.velocity:
            tick_eff = getattr(snapshot.velocity, "tick_efficiency", 1.0)

        if (
            adverse_enabled
            and velocity_pct > 70
            and displacement == "IMPULSE"
            and not trade_state.last_displacement_direction_favorable
            and trade_state.current_phase in ["ENTRY", "EXPANSION"]
            and trade_state.ticks_in_trade > adverse_min_ticks
            and trade_state.current_profit_pips < profit_protect_pips
            and tick_eff > 0.40
        ):
            urgency = self.config.get("adverse_impulse_urgency", 0.9) * htf_mult
            return ExitSignal(
                should_exit=True,
                action="CLOSE_NOW",
                reason=f"Adverse impulse: opposing velocity spike (eff={tick_eff:.2f})",
                urgency=min(1.0, urgency),
            )

        # --- RETEST TRAP GATE (CRITICAL) ---
        # If at_structure AND displacement hasn't flipped, don't exit
        if at_structure and trade_state.last_displacement_direction_favorable:
            return ExitSignal(
                should_exit=False,
                action="HOLD",
                reason="RETEST_TRAP: at_structure with favorable displacement, awaiting flip",
                urgency=0.0,
            )

        # --- PRIORITY 3: EXHAUSTION (medium-high urgency) ---
        # Gated by config. When disabled, an exhaustion stall no longer closes the trade;
        # the position is handed to VelocityTrailingManager / the defined SL+TP to book
        # profit instead. Disabled deliberately (2026-07-20) to test whether trades ride
        # further without the exhaustion cut.
        exhaustion_enabled = self.config.get("exit_engine_enable_exhaustion", True)
        exhaustion_vel_max = self.config.get("exhaustion_detection_velocity_max", 30)
        exhaustion_disp_max = self.config.get("exhaustion_detection_displacement_max", 0.3)

        # Check decay ratio defensively
        decay_ratio = 0.0
        if snapshot and hasattr(snapshot, "velocity") and snapshot.velocity:
            decay_ratio = getattr(snapshot.velocity, "decay_ratio", 0.0)

        if (
            exhaustion_enabled
            and trade_state.current_phase == "EXHAUSTION"
            and velocity_pct < exhaustion_vel_max
            and decay_ratio >= 0.80
            and abs(trade_state.current_profit_pips / max(mfe, 1.0)) < exhaustion_disp_max
            and at_structure
        ):
            urgency = self.config.get("exhaustion_urgency", 0.7) * htf_mult
            return ExitSignal(
                should_exit=True,
                action="CLOSE_NOW",
                reason=f"Exhaustion: low velocity stall (decay={decay_ratio:.2f}) near structure",
                urgency=min(1.0, urgency),
            )

        # --- PRIORITY 4: TRAILING STOP (REMOVED) ---
        # VelocityTrailingManager is now the sole authority for SL adjustments.
        # The legacy AdaptiveExitManager trail logic is no longer invoked here
        # to prevent two competing trail systems from racing each other and
        # ratcheting the stop too tight (see implementation_plan Bug 2).

        # --- DEFAULT: HOLD ---
        return ExitSignal(should_exit=False, action="HOLD", reason="All conditions favorable", urgency=0.0)


if __name__ == "__main__":
    # Smoke test (without requiring real AdaptiveExitManager)

    # Fake legacy manager
    class FakeLegacyExit:
        def evaluate(self, **kwargs):
            return None

    # Fake snapshot
    class FakeVelocity:
        percentile = 75.0
        is_unusual = True

    class FakeDisplacement:
        classification = "IMPULSE"
        net_displacement_pips = 3.0

    class FakeSnapshot:
        velocity = FakeVelocity()
        displacement = FakeDisplacement()

    # Fake trade state
    class FakeTradeState:
        is_active = True
        thesis_status = "CONFIRMED"
        last_velocity_percentile = 75.0
        last_displacement = "IMPULSE"
        last_displacement_direction_favorable = True
        current_phase = "EXPANSION"
        ticks_in_trade = 10
        mfe = 20.0
        current_profit_pips = 15.0
        htf_context = "NEUTRAL"

    # Fake location context
    class FakeLocationContext:
        at_structure = False

    engine = ExitEngine(legacy_exit_manager=FakeLegacyExit(), pip_mult=0.0001, config={})

    snapshot = FakeSnapshot()
    trade_state = FakeTradeState()
    location_ctx = FakeLocationContext()

    # Test 1: Normal expansion (favorable conditions) -> HOLD
    signal = engine.evaluate(trade_state, snapshot, location_ctx, current_price=1.0805)
    test1_pass = signal.should_exit == False and signal.action == "HOLD"
    print(f"  Test 1 (favorable expansion): {'PASS' if test1_pass else 'FAIL'}")

    # Test 2: Retest trap gate (at_structure + favorable disp) -> HOLD (not EXIT)
    location_ctx.at_structure = True
    signal = engine.evaluate(trade_state, snapshot, location_ctx, current_price=1.0805)
    test2_pass = "RETEST_TRAP" in signal.reason and signal.should_exit == False
    print(f"  Test 2 (retest trap gate): {'PASS' if test2_pass else 'FAIL'}")

    # Test 3: Adverse impulse (opposite direction) on a non-winning trade -> CLOSE_NOW
    trade_state.last_displacement_direction_favorable = False
    trade_state.current_phase = "ENTRY"
    trade_state.current_profit_pips = -2.0  # not yet profitable -> not protected
    trade_state.ticks_in_trade = 50  # past adverse_impulse_min_ticks (30) min-hold
    signal = engine.evaluate(trade_state, snapshot, location_ctx, current_price=1.0805)
    test3_pass = signal.should_exit == True and signal.reason.startswith("Adverse")
    print(f"  Test 3 (adverse impulse): {'PASS' if test3_pass else 'FAIL'}")

    # Test 4: Thesis broken
    trade_state.thesis_status = "BROKEN"
    trade_state.current_phase = "COMPRESSION"
    trade_state.last_displacement = "TRAP"
    signal = engine.evaluate(trade_state, snapshot, location_ctx, current_price=1.0805)
    test4_pass = signal.should_exit == True and "Thesis" in signal.reason
    print(f"  Test 4 (thesis failure): {'PASS' if test4_pass else 'FAIL'}")

    all_pass = test1_pass and test2_pass and test3_pass and test4_pass
    print(f"\nexit_engine.py: {'PASSED' if all_pass else 'FAILED'}")
    if not all_pass:
        exit(1)
