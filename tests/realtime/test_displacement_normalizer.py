"""Unit tests for DisplacementNormalizer."""

import pytest
from axonai.realtime.displacement_normalizer import DisplacementNormalizer, NormalizedDisplacement


class TestDisplacementNormalizer:
    """Test suite for DisplacementNormalizer class."""

    def test_bootstrap_constraint_insufficient_samples(self):
        """With <50 samples, z_score should be 0.0 (use static thresholds)."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Add 49 samples
        for i in range(49):
            result = norm.update(ratio=0.30, timestamp=float(1000 + i))

        # 49th sample should still have z_score=0
        assert result.sample_count == 49
        assert result.z_score == 0.0

        # 50th sample should now calculate z_score
        result = norm.update(ratio=0.50, timestamp=1049.0)
        assert result.sample_count == 50
        assert result.z_score != 0.0  # Should now be (0.50 - 0.30) / stdev

    def test_z_score_calculation_basic(self):
        """Verify z_score = (ratio - mean) / stdev."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Create 50 samples with known statistics (all 0.30)
        for i in range(50):
            norm.update(ratio=0.30, timestamp=float(1000 + i))

        # When all samples are identical, stdev = 0, z_score should be 0
        result = norm.update(ratio=0.30, timestamp=1050.0)
        assert result.rolling_mean == pytest.approx(0.30, abs=0.01)
        assert result.rolling_stdev == pytest.approx(0.0, abs=0.001)
        assert result.z_score == 0.0

        # Test with variance: fill with varied ratios
        norm2 = DisplacementNormalizer(window_sec=300.0)
        for i in range(50):
            norm2.update(ratio=0.30, timestamp=float(2000 + i))

        # Add outlier: 0.60 (above mean, should be positive z)
        result2 = norm2.update(ratio=0.60, timestamp=2050.0)
        assert result2.z_score > 0.5

    def test_rolling_window_time_based(self):
        """Only ratios from last 300s should be included."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Add samples from t=1000 to t=1049 (50 samples)
        for i in range(50):
            norm.update(ratio=0.30, timestamp=float(1000 + i))

        result = norm.update(ratio=0.30, timestamp=1050.0)
        assert result.sample_count == 51
        assert result.rolling_mean == pytest.approx(0.30, abs=0.01)

        # Jump 300 seconds forward (now at t=1350)
        result2 = norm.update(ratio=0.50, timestamp=1350.0)

        # Oldest samples should be pruned
        assert result2.sample_count < 51

    def test_reset_clears_history(self):
        """reset() should clear window and running stats."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Add 100 samples
        for i in range(100):
            norm.update(ratio=0.30, timestamp=float(1000 + i))

        # Call reset
        norm.reset()

        # Next update should have sample_count=1 (fresh start)
        result = norm.update(ratio=0.50, timestamp=1100.0)
        assert result.sample_count == 1
        assert result.z_score == 0.0  # <50 samples

    def test_choppy_market_low_mean(self):
        """Asia session: mean=0.28, ratio=0.40 -> z>1.0 (IMPULSE)."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Simulate Asia session: ratios 0.20-0.35 (choppy)
        asia_ratios = [0.21, 0.28, 0.35, 0.29, 0.32, 0.25, 0.30, 0.27, 0.33, 0.26]

        # Fill to 50 samples
        for i in range(50):
            idx = i % len(asia_ratios)
            norm.update(ratio=asia_ratios[idx], timestamp=float(1000 + i))

        # New reversal at 0.40 (higher than recent range)
        result = norm.update(ratio=0.40, timestamp=1050.0)

        # mean should be ~0.28-0.30
        assert 0.25 < result.rolling_mean < 0.32

        # z-score should be > 1.0 (at least 1 sigma above mean)
        assert result.z_score > 1.0

    def test_clean_market_high_mean(self):
        """London session: mean=0.55, ratio=0.50 -> z<-1.5 (TRAP)."""
        norm = DisplacementNormalizer(window_sec=300.0)

        # Simulate London session: ratios 0.50-0.62 (clean impulses)
        london_ratios = [0.52, 0.58, 0.61, 0.55, 0.59, 0.54, 0.60, 0.56, 0.62, 0.51]

        # Fill to 50 samples
        for i in range(50):
            idx = i % len(london_ratios)
            norm.update(ratio=london_ratios[idx], timestamp=float(2000 + i))

        # New move at 0.50 (lower than recent range)
        result = norm.update(ratio=0.50, timestamp=2050.0)

        # mean should be ~0.55-0.57
        assert 0.53 < result.rolling_mean < 0.60

        # z-score should be < -1.0 (below mean)
        assert result.z_score < -1.0
