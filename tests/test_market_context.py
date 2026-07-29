"""Test MarketContext and quality score calculations.

TDD approach: RED (failing tests) → GREEN (minimal code to pass) → REFACTOR
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from axonai.realtime.market_context import MarketContext, build_market_context_summary
from axonai.realtime.market_context_builder import MarketContextBuilder
from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.location_engine import LocationContext
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import (
    RegimeState,
    REGIME_TREND_EXPANSION,
    REGIME_TREND_CONTINUATION,
    REGIME_RANGE_CHOP,
    REGIME_COMPRESSION,
    REGIME_BREAKOUT,
)


class TestMarketContext:
    """Test MarketContext immutability and structure."""

    def test_market_context_is_frozen(self):
        """MarketContext dataclass must be frozen (immutable)."""
        ctx = self._make_context()

        # Should not allow modification
        with pytest.raises((AttributeError, TypeError)):
            ctx.price = 1.1000

        with pytest.raises((AttributeError, TypeError)):
            ctx.reversal_confidence = 95.0

    def test_market_context_captures_all_engine_outputs(self):
        """MarketContext includes all 6 math engine outputs."""
        ctx = self._make_context()

        # Should have all engines
        assert ctx.velocity is not None
        assert ctx.displacement is not None
        assert ctx.liquidity is not None
        assert ctx.location is not None
        assert ctx.mtf is not None
        assert ctx.regime is not None

    def test_market_context_summary_generation(self):
        """Summary should be human-readable."""
        ctx = self._make_context()
        summary = build_market_context_summary(ctx)

        # Should contain key info
        assert "Price:" in summary
        assert "Regime:" in summary
        assert "Confidence:" in summary
        assert "Verdict:" in summary

    # ========== HELPER ==========

    def _make_context(self) -> MarketContext:
        """Create a basic MarketContext for testing."""
        return MarketContext(
            timestamp=datetime.now(),
            price=1.0800,
            bid=1.0799,
            ask=1.0801,
            volume=100000,
            velocity=MagicMock(spec=NormalizedVelocity, percentile=60.0, is_unusual=False),
            displacement=MagicMock(
                spec=DisplacementState, classification="NEUTRAL", net_displacement_pips=0.0, displacement_ratio=0.5
            ),
            liquidity=MagicMock(spec=LiquidityState, active_sweeps=[]),
            location=MagicMock(
                spec=LocationContext, at_structure=False, distance_to_sr=10.0, room_available=50.0
            ),
            mtf=MagicMock(spec=MTFState, h1_bias=0.0, h4_bias=0.0, alignment_score=0.0),
            regime=MagicMock(spec=RegimeState, regime=REGIME_RANGE_CHOP, confidence=0.6),
            reversal_confidence=50.0,
            signal_agreement_score=50.0,
            consensus_verdict="AMBIGUOUS",
        )


class TestMarketContextBuilder:
    """Test quality score calculation logic."""

    # ========== TEST SIGNAL AGREEMENT SCORING ==========

    def test_signal_agreement_all_engines_agree(self):
        """When all 6 engines signal reversal, agreement should be 100%."""
        velocity = self._make_velocity(percentile=85, is_unusual=True)
        displacement = self._make_displacement(classification="IMPULSE", net_displacement_pips=10.0)
        location = self._make_location(at_structure=True, distance=2.0)
        mtf = self._make_mtf(h1_bias=50, h4_bias=60)
        regime = self._make_regime(REGIME_TREND_EXPANSION, confidence=0.9)
        liquidity = self._make_liquidity(active_sweeps=["sweep1"])

        builder = MarketContextBuilder()
        agreement, agree_list, disagree_list = builder._calculate_signal_agreement(
            velocity, displacement, location, mtf, regime, liquidity
        )

        assert agreement == 100.0, f"Expected 100%, got {agreement}%"
        assert len(agree_list) >= 5, f"Expected 5+ agreeing signals, got {agree_list}"
        assert len(disagree_list) == 0, f"Expected no disagreeing signals, got {disagree_list}"

    def test_signal_agreement_partial_agreement(self):
        """When some engines agree, score should reflect the ratio."""
        velocity = self._make_velocity(percentile=45, is_unusual=False)  # Disagree
        displacement = self._make_displacement(classification="IMPULSE")  # Agree
        location = self._make_location(at_structure=True, distance=2.0)  # Agree
        mtf = self._make_mtf(h1_bias=5, h4_bias=8)  # Disagree (low bias)
        regime = self._make_regime(REGIME_RANGE_CHOP, confidence=0.5)  # Disagree
        liquidity = self._make_liquidity(active_sweeps=[])  # Disagree

        builder = MarketContextBuilder()
        agreement, agree_list, disagree_list = builder._calculate_signal_agreement(
            velocity, displacement, location, mtf, regime, liquidity
        )

        # Should be ~33% (2 out of 6 agree)
        assert 25 < agreement < 40, f"Expected ~33%, got {agreement}%"
        assert len(agree_list) == 2, f"Expected 2 agreeing, got {agree_list}"

    def test_signal_agreement_no_agreement(self):
        """When no engines agree, score should be low."""
        velocity = self._make_velocity(percentile=30, is_unusual=False)
        displacement = self._make_displacement(classification="NEUTRAL")
        location = self._make_location(at_structure=False, distance=20.0)
        mtf = self._make_mtf(h1_bias=5, h4_bias=5)
        regime = self._make_regime(REGIME_RANGE_CHOP)
        liquidity = self._make_liquidity(active_sweeps=[])

        builder = MarketContextBuilder()
        agreement, agree_list, disagree_list = builder._calculate_signal_agreement(
            velocity, displacement, location, mtf, regime, liquidity
        )

        assert agreement < 20.0, f"Expected <20%, got {agreement}%"

    # ========== TEST REVERSAL CONFIDENCE SCORING ==========

    def test_reversal_confidence_strong_signal(self):
        """High velocity + impulse displacement + at major level = high confidence."""
        velocity = self._make_velocity(percentile=95, is_unusual=True)
        displacement = self._make_displacement(classification="IMPULSE", net_displacement_pips=15.0)
        location = self._make_location(at_structure=True, distance=1.0)
        mtf = self._make_mtf(h1_bias=70, h4_bias=75)
        regime = self._make_regime(REGIME_TREND_EXPANSION, confidence=0.95)

        builder = MarketContextBuilder()
        confidence = builder._calculate_reversal_confidence(velocity, displacement, location, mtf, regime, 90.0)

        assert confidence > 80, f"Expected >80 for strong signal, got {confidence}"

    def test_reversal_confidence_weak_signal(self):
        """Low velocity + neutral displacement + random location = low confidence."""
        velocity = self._make_velocity(percentile=30, is_unusual=False)
        displacement = self._make_displacement(classification="NEUTRAL", net_displacement_pips=0.5)
        location = self._make_location(at_structure=False, distance=30.0)
        mtf = self._make_mtf(h1_bias=0, h4_bias=0)
        regime = self._make_regime(REGIME_RANGE_CHOP, confidence=0.5)

        builder = MarketContextBuilder()
        confidence = builder._calculate_reversal_confidence(velocity, displacement, location, mtf, regime, 10.0)

        assert confidence < 40, f"Expected <40 for weak signal, got {confidence}"

    def test_reversal_confidence_scale_0_to_100(self):
        """Confidence score should always be 0-100."""
        builder = MarketContextBuilder()

        # Max confidence scenario
        velocity = self._make_velocity(percentile=95, is_unusual=True)
        displacement = self._make_displacement(classification="IMPULSE")
        location = self._make_location(at_structure=True, distance=1.0)
        mtf = self._make_mtf(h1_bias=80, h4_bias=80)
        regime = self._make_regime(REGIME_TREND_EXPANSION, confidence=0.95)

        conf_max = builder._calculate_reversal_confidence(velocity, displacement, location, mtf, regime, 100.0)
        assert 0 <= conf_max <= 100, f"Confidence {conf_max} out of range"

    # ========== TEST STOP HUNTING DETECTION ==========

    def test_stop_hunting_detected_with_active_sweeps(self):
        """Multiple active sweeps with low displacement = stop hunting."""
        liquidity = self._make_liquidity(active_sweeps=["sweep1", "sweep2"])
        displacement = self._make_displacement(classification="EXHAUSTION", net_displacement_pips=1.0, displacement_ratio=0.2)

        builder = MarketContextBuilder()
        detected, severity, phase = builder._detect_stop_hunting(1.0800, liquidity, displacement)

        assert detected is True, "Should detect stop hunting"
        assert severity > 30, f"Expected severity >30, got {severity}"
        assert phase in ("HUNTING", "SWEEPING", "REVERSING"), f"Unexpected phase: {phase}"

    def test_stop_hunting_not_detected_clean_move(self):
        """Clean move with good displacement = no stop hunting."""
        liquidity = self._make_liquidity(active_sweeps=[])
        displacement = self._make_displacement(classification="IMPULSE", net_displacement_pips=20.0, displacement_ratio=0.8)

        builder = MarketContextBuilder()
        detected, severity, phase = builder._detect_stop_hunting(1.0800, liquidity, displacement)

        assert detected is False, "Should not detect stop hunting on clean impulse"
        assert severity < 30, f"Expected severity <30, got {severity}"

    # ========== TEST CONSENSUS VERDICT ==========

    def test_consensus_verdict_strong_reversal(self):
        """Confidence 80+ AND agreement 75+ = STRONG_REVERSAL."""
        builder = MarketContextBuilder()
        verdict = builder._determine_consensus_verdict(confidence=85, agreement=80)

        assert verdict == "STRONG_REVERSAL"

    def test_consensus_verdict_moderate_reversal(self):
        """Confidence 60-79 AND agreement 60+ = MODERATE_REVERSAL."""
        builder = MarketContextBuilder()
        verdict = builder._determine_consensus_verdict(confidence=70, agreement=65)

        assert verdict == "MODERATE_REVERSAL"

    def test_consensus_verdict_weak_reversal(self):
        """Confidence 40-59 = WEAK_REVERSAL."""
        builder = MarketContextBuilder()
        verdict = builder._determine_consensus_verdict(confidence=50, agreement=50)

        assert verdict == "WEAK_REVERSAL"

    def test_consensus_verdict_ambiguous(self):
        """Confidence <40 = AMBIGUOUS."""
        builder = MarketContextBuilder()
        verdict = builder._determine_consensus_verdict(confidence=30, agreement=40)

        assert verdict == "AMBIGUOUS"

    # ========== TEST LAG DETECTION ==========

    def test_reversal_lag_immediate(self):
        """Ticks in phase 0-1 = no lag."""
        velocity = self._make_velocity()
        displacement = self._make_displacement()

        builder = MarketContextBuilder()
        lag_ticks, is_lagged, severity = builder._estimate_reversal_lag(velocity, displacement, ticks_in_phase=0)

        assert lag_ticks == 0
        assert is_lagged is False
        assert severity == "NONE"

    def test_reversal_lag_heavy(self):
        """Ticks in phase 6+ = heavy lag."""
        velocity = self._make_velocity()
        displacement = self._make_displacement()

        builder = MarketContextBuilder()
        lag_ticks, is_lagged, severity = builder._estimate_reversal_lag(velocity, displacement, ticks_in_phase=8)

        assert lag_ticks == 8
        assert is_lagged is True
        assert severity == "HEAVY"

    # ========== TEST DISPLACEMENT PHASE TRACKING ==========

    def test_displacement_phase_early_on_first_tick(self):
        """First tick of displacement = EARLY."""
        builder = MarketContextBuilder()
        displacement = self._make_displacement(classification="IMPULSE")

        builder._update_displacement_phase(displacement)

        assert builder._displacement_phase_str == "EARLY"
        assert builder._ticks_in_current_displacement == 0

    def test_displacement_phase_confirming_after_2_ticks(self):
        """2 ticks in same displacement = CONFIRMING."""
        builder = MarketContextBuilder()
        displacement = self._make_displacement(classification="IMPULSE")

        builder._update_displacement_phase(displacement)  # Tick 1
        builder._update_displacement_phase(displacement)  # Tick 2
        builder._update_displacement_phase(displacement)  # Tick 3

        assert builder._displacement_phase_str == "CONFIRMING"

    def test_displacement_phase_confirmed_after_4_ticks(self):
        """4+ ticks in same displacement = CONFIRMED."""
        builder = MarketContextBuilder()
        displacement = self._make_displacement(classification="IMPULSE")

        for _ in range(5):
            builder._update_displacement_phase(displacement)

        assert builder._displacement_phase_str == "CONFIRMED"

    def test_displacement_phase_resets_on_classification_change(self):
        """When displacement changes, phase resets to EARLY."""
        builder = MarketContextBuilder()
        displacement1 = self._make_displacement(classification="IMPULSE")
        displacement2 = self._make_displacement(classification="NEUTRAL")

        builder._update_displacement_phase(displacement1)
        builder._update_displacement_phase(displacement1)
        builder._update_displacement_phase(displacement2)  # Classification changed

        assert builder._displacement_phase_str == "EARLY"
        assert builder._ticks_in_current_displacement == 0

    # ========== HELPERS ==========

    def _make_velocity(self, percentile=60.0, is_unusual=False) -> NormalizedVelocity:
        vel = MagicMock(spec=NormalizedVelocity)
        vel.percentile = percentile
        vel.is_unusual = is_unusual
        vel.is_decaying = False
        return vel

    def _make_displacement(
        self, classification="NEUTRAL", net_displacement_pips=0.0, displacement_ratio=0.5
    ) -> DisplacementState:
        disp = MagicMock(spec=DisplacementState)
        disp.classification = classification
        disp.net_displacement_pips = net_displacement_pips
        disp.displacement_ratio = displacement_ratio
        return disp

    def _make_liquidity(self, active_sweeps=None) -> LiquidityState:
        liq = MagicMock(spec=LiquidityState)
        liq.active_sweeps = active_sweeps or []
        return liq

    def _make_location(self, at_structure=False, distance=10.0) -> LocationContext:
        loc = MagicMock(spec=LocationContext)
        loc.at_structure = at_structure
        loc.distance_to_sr = distance
        loc.room_available = 50.0
        loc.nearest_level_type = "RESISTANCE" if distance > 0 else "SUPPORT"
        loc.nearest_level_price = 1.0810
        return loc

    def _make_mtf(self, h1_bias=0.0, h4_bias=0.0) -> MTFState:
        mtf = MagicMock(spec=MTFState)
        mtf.h1_bias = h1_bias
        mtf.h4_bias = h4_bias
        mtf.alignment_score = abs(h1_bias) + abs(h4_bias)
        mtf.is_pullback = False
        return mtf

    def _make_regime(self, regime=REGIME_RANGE_CHOP, confidence=0.6) -> RegimeState:
        reg = MagicMock(spec=RegimeState)
        reg.regime = regime
        reg.confidence = confidence
        return reg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
