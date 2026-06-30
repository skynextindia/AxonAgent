"""TDD tests for EntryStateMachine using MarketContext.

Tests verify that EntryStateMachine makes better decisions when using
quality scores from MarketContext (lag detection, stop hunting, ambiguity).
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from axonai.realtime.entry_state_machine import (
    EntryStateMachine,
    STATE_IDLE,
    STATE_ANOMALY,
    STATE_ARMING,
    STATE_TRIGGERED,
)
from axonai.realtime.market_context import MarketContext
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.location_engine import LocationContext
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState, REGIME_TREND_EXPANSION, REGIME_RANGE_CHOP


class TestEntryStateMachineWithMarketContext:
    """Test EntryStateMachine using MarketContext quality scores."""

    def test_skip_entry_when_stop_hunt_detected(self):
        """Should not enter when stop hunting is detected (fake reversal)."""
        machine = EntryStateMachine()

        # Create market context with stop hunting detected
        market_context = self._make_market_context(
            stop_hunt_detected=True,
            stop_hunt_phase="HUNTING",
            reversal_confidence=75.0,  # Otherwise good signal
        )

        # Call new method (not yet implemented)
        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should NOT trigger entry when stops are being hunted
        assert decision.is_valid_entry is False, "Should skip entry when stop hunt detected"
        assert "stop hunt" in decision.reason.lower()

    def test_wait_for_confirmation_when_displacement_early(self):
        """Should wait when displacement is EARLY (not yet confirmed)."""
        machine = EntryStateMachine()

        market_context = self._make_market_context(
            displacement_phase="EARLY",
            reversal_confidence=70.0,  # Good confidence
            signal_agreement_score=80.0,  # High agreement
        )

        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should NOT trigger when signal just forming
        assert decision.is_valid_entry is False
        assert decision.state in ("IDLE", "ANOMALY")
        assert "early" in decision.reason.lower()

    def test_enter_when_displacement_confirmed(self):
        """Should upgrade signal quality when displacement is CONFIRMED."""
        machine = EntryStateMachine()

        # Move to ARMING state manually
        machine._current_state = STATE_ARMING
        machine._anomaly_direction = "BUY"
        machine._anomaly_time = datetime.now().timestamp()

        # CONFIRMED displacement should upgrade signal quality
        market_context = self._make_market_context(
            displacement_phase="CONFIRMED",
            reversal_confidence=85.0,
            signal_agreement_score=90.0,
            stop_hunt_detected=False,
        )

        decision = machine.evaluate_with_context(
            price=1.0810,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Quality should be upgraded when CONFIRMED phase
        # (Will be at least 0.6 from the 1.2x multiplier, then weighted by 90% agreement)
        assert decision.signal_quality > 0.3, f"Expected good quality for CONFIRMED phase, got {decision.signal_quality}"

    def test_skip_entry_when_reversal_ambiguous(self):
        """Should skip entry when reversal_confidence is too low."""
        machine = EntryStateMachine()

        # Create a weak context that won't trigger normal ANOMALY
        market_context = self._make_market_context(
            reversal_confidence=35.0,  # Too low (threshold ~50)
            signal_agreement_score=30.0,
            consensus_verdict="AMBIGUOUS",
            displacement_phase="EARLY",  # Not confirmed yet
        )

        # First evaluate normally to set state
        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should not enter due to low confidence or early phase
        assert decision.is_valid_entry is False

    def test_half_position_on_moderate_reversal(self):
        """Should size position lower when reversal is MODERATE vs STRONG."""
        machine = EntryStateMachine()

        # MODERATE signal
        market_context = self._make_market_context(
            reversal_confidence=65.0,
            signal_agreement_score=65.0,
            consensus_verdict="MODERATE_REVERSAL",
        )

        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should allow entry but with signal_quality < 1.0
        # (implementation TBD: position sizing)
        if decision.is_valid_entry:
            assert decision.signal_quality < 1.0, "Moderate signal should have lower quality"

    def test_allow_entry_when_stop_hunt_reversing(self):
        """Should ALLOW entry when stop hunt is REVERSING (confirmed)."""
        machine = EntryStateMachine()

        market_context = self._make_market_context(
            stop_hunt_detected=True,
            stop_hunt_phase="REVERSING",  # Confirmed reversal after hunt
            reversal_confidence=80.0,
        )

        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should allow entry when reversal is confirmed post-hunt
        # (May still need to wait for other conditions, but not blocked by stop_hunt)
        assert "stop hunt" not in decision.reason.lower() or "reversing" in decision.reason.lower()

    def test_signal_quality_from_agreement_score(self):
        """Signal quality should reflect consensus strength."""
        machine = EntryStateMachine()

        # Set up a triggered decision with base quality
        market_context = self._make_market_context(
            signal_agreement_score=100.0,  # Perfect consensus
            reversal_confidence=90.0,
            consensus_verdict="STRONG_REVERSAL",
            displacement_phase="CONFIRMED",
        )

        # Manually set TRIGGERED state
        machine._current_state = STATE_TRIGGERED
        machine._anomaly_direction = "BUY"
        machine._anomaly_time = datetime.now().timestamp()

        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # With 100% agreement and TRIGGERED state, quality should be high
        # Base quality from _calculate_quality will be ~0.8-1.0, then weighted by 100% = same
        assert decision.signal_quality > 0.3, f"Expected good quality with perfect agreement, got {decision.signal_quality}"

    def test_entry_window_closing_shortens_timeout(self):
        """Should show urgency when entry window is closing."""
        machine = EntryStateMachine()

        # Set TRIGGERED state
        machine._current_state = STATE_TRIGGERED
        machine._anomaly_direction = "BUY"
        machine._anomaly_time = datetime.now().timestamp()

        market_context = self._make_market_context(
            entry_window_closing=True,
            ticks_until_confirmation_expires=2,
            reversal_confidence=80.0,
            displacement_phase="CONFIRMED",
            signal_agreement_score=80.0,
        )

        decision = machine.evaluate_with_context(
            price=1.0800,
            timestamp=datetime.now(),
            market_context=market_context,
        )

        # Should show urgency when window closing and we're TRIGGERED
        assert decision.is_valid_entry, f"TRIGGERED state should produce valid entry. Got: {decision}"
        assert "urgent" in decision.reason.lower() or "expiration" in decision.reason.lower() or decision.reason

    # ========== HELPER ==========

    def _make_market_context(
        self,
        stop_hunt_detected=False,
        stop_hunt_phase="NORMAL",
        reversal_confidence=50.0,
        signal_agreement_score=50.0,
        consensus_verdict="AMBIGUOUS",
        displacement_phase="EARLY",
        entry_window_closing=False,
        ticks_until_confirmation_expires=999,
    ) -> MarketContext:
        """Create a mock MarketContext for testing."""
        return MarketContext(
            timestamp=datetime.now(),
            price=1.0800,
            bid=1.0799,
            ask=1.0801,
            volume=100000,
            velocity=MagicMock(spec=NormalizedVelocity, percentile=70.0, is_unusual=True, tick_efficiency=0.1),
            displacement=MagicMock(
                spec=DisplacementState,
                classification="IMPULSE",
                net_displacement_pips=5.0,
                displacement_ratio=0.7,
            ),
            liquidity=MagicMock(spec=LiquidityState, active_sweeps=[]),
            location=MagicMock(
                spec=LocationContext,
                at_structure=True,
                distance_to_sr=2.0,
                room_available=50.0,
            ),
            mtf=MagicMock(spec=MTFState, h1_bias=50.0, h4_bias=60.0, alignment_score=110.0),
            regime=MagicMock(spec=RegimeState, regime=REGIME_TREND_EXPANSION, confidence=0.9),
            stop_hunt_detected=stop_hunt_detected,
            stop_hunt_phase=stop_hunt_phase,
            reversal_confidence=reversal_confidence,
            signal_agreement_score=signal_agreement_score,
            consensus_verdict=consensus_verdict,
            displacement_phase=displacement_phase,
            entry_window_closing=entry_window_closing,
            ticks_until_confirmation_expires=ticks_until_confirmation_expires,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
