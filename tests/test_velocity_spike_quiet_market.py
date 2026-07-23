"""Test velocity spike detection in quiet markets (RANGE_CHOP regime).

Scenario: System should detect velocity spikes above 36th percentile threshold
in quiet markets and transition: IDLE → ANOMALY → ARMING → TRIGGERED.

Test coverage:
1. IDLE state waits for velocity spike anomaly in quiet market
2. ANOMALY state triggered when velocity exceeds 36th percentile
3. ARMING state entered when displacement shows absorption/trap
4. TRIGGERED state reached when price breaks away from trap with impulse
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from axonai.realtime.entry_state_machine import (
    EntryStateMachine,
    STATE_IDLE,
    STATE_ANOMALY,
    STATE_ARMING,
    STATE_RETEST_WAIT,
    STATE_TRIGGERED,
    STATE_INVALIDATED,
)
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import (
    DisplacementState,
    DISPLACEMENT_TRAP,
    DISPLACEMENT_ABSORPTION,
    DISPLACEMENT_IMPULSE,
    DISPLACEMENT_NEUTRAL,
)
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState, REGIME_RANGE_CHOP


# These tests cover TICK-LEVEL spike detection, which is upstream of the M15
# candle-setup gate. That gate defaults on and returns from _evaluate_idle before
# any velocity logic runs when no setup is active, so every case here stayed in
# IDLE. Disabling it isolates the path under test rather than asserting against
# the gate's own behaviour, which test_candle_setup_gate covers separately.
_TICK_ONLY = {"candle_setup_gate": False}


class TestVelocitySpikeQuietMarket:
    """Velocity spike detection in RANGE_CHOP (quiet) market."""

    def _make_velocity(self, percentile: float = 0.0, tick_efficiency: float = 0.5, is_unusual: bool = True,
                       decay_ratio: float = 1.0, tick_rate_10s: float = 0.0, tick_rate_300s: float = 0.0,
                       vol_pips: float = 3.0):
        """Create a mock NormalizedVelocity."""
        vel = MagicMock(spec=NormalizedVelocity)
        vel.percentile = percentile
        vel.tick_efficiency = tick_efficiency
        vel.is_unusual = is_unusual
        vel.is_decaying = False
        vel.decay_ratio = decay_ratio
        vel.tick_rate_10s = tick_rate_10s
        vel.tick_rate_300s = tick_rate_300s
        vel.vol_pips = vol_pips
        return vel

    def _make_displacement(self, classification: str, net_displacement_pips: float = 5.0):
        """Create a mock DisplacementState."""
        disp = MagicMock(spec=DisplacementState)
        disp.classification = classification
        disp.net_displacement_pips = net_displacement_pips
        disp.displacement_ratio = 0.5
        return disp

    def _make_liquidity(self, active_sweeps=None):
        """Create a mock LiquidityState."""
        liq = MagicMock(spec=LiquidityState)
        liq.active_sweeps = active_sweeps or []
        return liq

    def _make_regime(self, regime: str = REGIME_RANGE_CHOP, confidence: float = 0.9):
        """Create a mock RegimeState."""
        regime_obj = MagicMock(spec=RegimeState)
        regime_obj.regime = regime
        regime_obj.confidence = confidence
        return regime_obj

    def _make_mtf(self):
        """Create a neutral MTF context."""
        mtf = MagicMock(spec=MTFState)
        mtf.h1_bias = 0.0
        mtf.h4_bias = 0.0
        mtf.alignment_score = 0.0
        mtf.is_pullback = False
        return mtf

    def test_idle_state_initial(self):
        """Machine starts in IDLE state."""
        fsm = EntryStateMachine(config=_TICK_ONLY)
        assert fsm._current_state == STATE_IDLE

    def test_velocity_spike_above_36th_percentile_triggers_anomaly(self):
        """Velocity spike above 36th percentile in quiet market transitions to ANOMALY.

        When regime is RANGE_CHOP (quiet market):
        - Threshold is 36th percentile (90 * 0.40 = 36.0)
        - Velocity percentile 50 > 36 → is_unusual = True ✓
        - Low tick efficiency (climax) → anomaly detected
        """
        fsm = EntryStateMachine(config=_TICK_ONLY)

        # Quiet market: RANGE_CHOP regime
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        # Price action: bullish climax (upward displacement + low efficiency)
        velocity = self._make_velocity(
            percentile=50.0,  # Above 36th threshold
            tick_efficiency=0.15,  # Low efficiency = climax
            is_unusual=True
        )
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=10.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        price = 1.10000
        now = datetime.now()

        decision = fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf)

        # Should transition to ANOMALY
        assert fsm._current_state == STATE_ANOMALY
        assert decision.state == STATE_ANOMALY
        assert not decision.is_valid_entry  # Not TRIGGERED yet
        assert decision.direction == "SELL"  # Expect reversal from bullish climax

    def test_anomaly_with_absorption_transitions_to_arming(self):
        """When displacement shows absorption/trap, transition ANOMALY → ARMING."""
        fsm = EntryStateMachine(config=_TICK_ONLY)
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        # First evaluation: detect anomaly
        velocity = self._make_velocity(percentile=50.0, tick_efficiency=0.15)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=10.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        price = 1.10000
        now = datetime.now()

        # Trigger ANOMALY
        fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf)
        assert fsm._current_state == STATE_ANOMALY

        # Second evaluation: absorption forms (trap/absorption classification)
        velocity = self._make_velocity(percentile=50.0, tick_efficiency=0.5, is_unusual=False)
        displacement = self._make_displacement(DISPLACEMENT_ABSORPTION, net_displacement_pips=5.0)

        decision = fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf)

        # Should transition to ARMING
        assert fsm._current_state == STATE_ARMING
        assert decision.state == STATE_ARMING

    def test_arming_with_impulse_breaks_away_to_retest_wait(self):
        """A plain impulse breakaway routes to RETEST_WAIT, not straight to TRIGGERED.

        A breakaway is a breakout, not yet a confirmed reversal. It has to come
        back and fail at the level before it counts. `entry_require_retest_confirm`
        (default True) is what enforces that; the next test covers it set False.
        """
        fsm = EntryStateMachine(config=_TICK_ONLY)
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        price = 1.10000
        now = datetime.now()

        velocity = self._make_velocity(percentile=50.0, tick_efficiency=0.15)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=10.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        # 1. Trigger ANOMALY (bullish climax, expect SELL)
        fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ANOMALY

        # 2. Enter ARMING (absorption detected)
        velocity = self._make_velocity(percentile=50.0, tick_efficiency=0.5)
        displacement = self._make_displacement(DISPLACEMENT_ABSORPTION, net_displacement_pips=5.0)
        fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ARMING

        # 3. Break away past the stall delay: price drops below the trigger distance.
        price = 1.09994
        velocity = self._make_velocity(percentile=60.0, tick_efficiency=0.5)
        displacement = self._make_displacement(DISPLACEMENT_IMPULSE, net_displacement_pips=-8.0)

        fsm.evaluate(price, now + timedelta(seconds=20), velocity, displacement,
                     liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_RETEST_WAIT

    def test_breakaway_triggers_directly_when_retest_confirm_disabled(self):
        """entry_require_retest_confirm=False restores the pre-2026-07-23 bypass."""
        fsm = EntryStateMachine(config={**_TICK_ONLY, "entry_require_retest_confirm": False})
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        price = 1.10000
        now = datetime.now()
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        fsm.evaluate(price, now, self._make_velocity(percentile=50.0, tick_efficiency=0.15),
                     self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=10.0),
                     liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ANOMALY

        fsm.evaluate(price, now, self._make_velocity(percentile=50.0, tick_efficiency=0.5),
                     self._make_displacement(DISPLACEMENT_ABSORPTION, net_displacement_pips=5.0),
                     liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ARMING

        decision = fsm.evaluate(1.09994, now + timedelta(seconds=20),
                                self._make_velocity(percentile=60.0, tick_efficiency=0.5),
                                self._make_displacement(DISPLACEMENT_IMPULSE, net_displacement_pips=-8.0),
                                liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_TRIGGERED
        assert decision.is_valid_entry is True
        assert decision.direction == "SELL"

    def test_full_cycle_idle_to_triggered_in_quiet_market(self):
        """Full state machine cycle in quiet market: IDLE → ANOMALY → ARMING → RETEST_WAIT → TRIGGERED."""
        fsm = EntryStateMachine(config=_TICK_ONLY)
        regime = self._make_regime(regime=REGIME_RANGE_CHOP, confidence=0.95)

        price = 1.10000
        base_time = datetime(2026, 6, 25, 10, 0, 0)

        # === TICK 1: Quiet market, no anomaly yet ===
        velocity = self._make_velocity(percentile=25.0, is_unusual=False)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=0.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        decision = fsm.evaluate(price, base_time, velocity, displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_IDLE
        assert not decision.is_valid_entry

        # === TICK 2: Velocity spike above 36th percentile (climax) ===
        price = 1.10005
        velocity = self._make_velocity(percentile=45.0, tick_efficiency=0.18, is_unusual=True)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=8.0)

        decision = fsm.evaluate(price, base_time, velocity, displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ANOMALY
        assert decision.state == STATE_ANOMALY
        assert decision.direction == "SELL"  # Reversal expected from bullish climax

        # === TICK 3: Absorption detected (trap forming) ===
        price = 1.10006
        velocity = self._make_velocity(percentile=40.0, tick_efficiency=0.5, is_unusual=False)
        displacement = self._make_displacement(DISPLACEMENT_ABSORPTION, net_displacement_pips=4.0)

        decision = fsm.evaluate(price, base_time, velocity, displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_ARMING
        assert decision.state == STATE_ARMING

        # === TICK 4: Price breaks away with impulse -> RETEST_WAIT ===
        # Past the 15s stall delay, otherwise the trigger is held in ARMING.
        price = 1.09994  # Down 1.1 pips from the anomaly level
        velocity = self._make_velocity(percentile=55.0, tick_efficiency=0.5)
        displacement = self._make_displacement(DISPLACEMENT_IMPULSE, net_displacement_pips=-10.0)

        decision = fsm.evaluate(price, base_time + timedelta(seconds=20), velocity,
                                displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_RETEST_WAIT

        # === TICK 5: Price pulls AWAY from the level (beyond the retest zone) ===
        # zone_width = max(2.0, vol_pips=3.0) = 3.0, so it must exceed 3 pips out.
        price = 1.09965  # 4.0 pips below the level
        velocity = self._make_velocity(percentile=50.0, tick_efficiency=0.5, decay_ratio=0.7)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=-6.0)

        fsm.evaluate(price, base_time + timedelta(seconds=30), velocity, displacement,
                     liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_RETEST_WAIT

        # === TICK 6: Price comes BACK toward the level and stalls -> TRIGGERED ===
        price = 1.09995  # back to 1.0 pips below the level, off its 4.0 extreme
        velocity = self._make_velocity(percentile=40.0, tick_efficiency=0.5, decay_ratio=0.3)
        displacement = self._make_displacement(DISPLACEMENT_ABSORPTION, net_displacement_pips=-3.0)

        decision = fsm.evaluate(price, base_time + timedelta(seconds=40), velocity,
                                displacement, liquidity, regime, mtf, spread=0.1 * 0.0001)
        assert fsm._current_state == STATE_TRIGGERED
        assert decision.is_valid_entry is True
        assert decision.direction == "SELL"
        assert decision.signal_quality > 0.0

    def test_quiet_market_threshold_boundary(self):
        """At exactly 36th percentile, velocity is NOT unusual yet."""
        fsm = EntryStateMachine(config=_TICK_ONLY)
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        # At 36th percentile exactly (boundary)
        velocity = self._make_velocity(percentile=36.0, tick_efficiency=0.15, is_unusual=False)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=5.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        price = 1.10000
        now = datetime.now()

        decision = fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf)

        # 36th percentile alone is not enough (is_unusual=False)
        assert fsm._current_state == STATE_IDLE
        assert not decision.is_valid_entry

    def test_above_threshold_with_is_unusual_flag_triggers_anomaly(self):
        """Above 36th percentile WITH is_unusual=True triggers anomaly."""
        fsm = EntryStateMachine(config=_TICK_ONLY)
        regime = self._make_regime(regime=REGIME_RANGE_CHOP)

        # 37th percentile (just above 36) with is_unusual=True
        velocity = self._make_velocity(percentile=37.0, tick_efficiency=0.15, is_unusual=True)
        displacement = self._make_displacement(DISPLACEMENT_NEUTRAL, net_displacement_pips=5.0)
        liquidity = self._make_liquidity()
        mtf = self._make_mtf()

        price = 1.10000
        now = datetime.now()

        decision = fsm.evaluate(price, now, velocity, displacement, liquidity, regime, mtf)

        # Just above threshold with is_unusual flag → ANOMALY
        assert fsm._current_state == STATE_ANOMALY
        assert decision.state == STATE_ANOMALY
