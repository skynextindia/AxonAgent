"""Tests for the cross-pair correlation engine (Phase 4 / F3).

The engine's decision logic is pure (no MT5), so these run offline. Each engine
has its refresh frozen so injected state (rolling_corr / lead_bias / positions)
is not overwritten by a data fetch.
"""

from __future__ import annotations

import unittest

from axonai.realtime.correlation_engine import (
    CorrelationEngine,
    position_usd,
    pearson,
)


def _engine(**cfg):
    base = {
        "corr_engine_enabled": True,
        "corr_lead_symbol": "EURUSD",
        "corr_refresh_seconds": 10_000,
        "corr_max_net_usd": 200000.0,
        "corr_veto_bias_threshold": 0.0015,
        "corr_size_scale_min": 0.25,
    }
    base.update(cfg)
    e = CorrelationEngine(["EURUSD", "USDJPY"], base)
    e._last_refresh = 1e18  # freeze: no MT5 refresh during tests
    return e


class TestPositionUsd(unittest.TestCase):
    def test_eurusd_long_is_short_usd(self):
        self.assertLess(position_usd("EURUSD", "BUY", 1.0, 1.10), 0)

    def test_eurusd_sell_is_long_usd(self):
        self.assertGreater(position_usd("EURUSD", "SELL", 1.0, 1.10), 0)

    def test_usdjpy_long_is_long_usd(self):
        self.assertGreater(position_usd("USDJPY", "BUY", 1.0, 150.0), 0)

    def test_usdjpy_sell_is_short_usd(self):
        self.assertLess(position_usd("USDJPY", "SELL", 1.0, 150.0), 0)


class TestPearson(unittest.TestCase):
    def test_perfect_positive(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0, places=6)

    def test_perfect_negative(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0, places=6)

    def test_short_series_zero(self):
        self.assertEqual(pearson([1, 2], [2, 4]), 0.0)


class TestEvaluateEntry(unittest.TestCase):
    def test_lead_pair_never_gated(self):
        e = _engine()
        e.rolling_corr, e.lead_bias = 0.9, 0.05
        allow, scale, _ = e.evaluate_entry("EURUSD", "Buy")
        self.assertTrue(allow)
        self.assertEqual(scale, 1.0)

    def test_veto_contradicting_usd_direction(self):
        # EURUSD strongly up (bias>0 → USD weak). Long USDJPY = long USD → veto.
        e = _engine()
        e.lead_bias = 0.01
        allow, _, reason = e.evaluate_entry("USDJPY", "Buy")
        self.assertFalse(allow)
        self.assertIn("vetoed", reason)

    def test_allow_agreeing_usd_direction(self):
        # EURUSD up (USD weak). Short USDJPY = short USD → agrees → allow.
        e = _engine()
        e.lead_bias, e.rolling_corr = 0.01, 0.0
        allow, _, _ = e.evaluate_entry("USDJPY", "Sell")
        self.assertTrue(allow)

    def test_size_scale_shrinks_with_correlation(self):
        e = _engine()
        e.lead_bias, e.rolling_corr = 0.0, 0.8
        allow, scale, _ = e.evaluate_entry("USDJPY", "Buy")
        self.assertTrue(allow)
        self.assertAlmostEqual(scale, 1.0 - 0.5 * 0.8, places=6)  # 0.6

    def test_exposure_cap_vetoes_when_at_limit(self):
        e = _engine(corr_max_net_usd=100000.0)
        e.lead_bias = 0.0
        e.register_position("USDJPY", "BUY", 1.0, 150.0, ticket=1)  # +100k USD
        allow, _, reason = e.evaluate_entry("USDJPY", "Buy")        # stacks → veto
        self.assertFalse(allow)
        self.assertIn("at cap", reason)

    def test_exposure_hedge_allowed(self):
        e = _engine(corr_max_net_usd=100000.0)
        e.lead_bias, e.rolling_corr = 0.0, 0.0
        e.register_position("USDJPY", "BUY", 1.0, 150.0, ticket=1)  # +100k USD
        allow, scale, _ = e.evaluate_entry("USDJPY", "Sell")        # reduces net → allow
        self.assertTrue(allow)
        self.assertEqual(scale, 1.0)

    def test_net_usd_exposure_nets_out(self):
        e = _engine()
        e.register_position("USDJPY", "BUY", 1.0, 150.0, ticket=1)   # +100k
        e.register_position("USDJPY", "SELL", 1.0, 150.0, ticket=2)  # -100k
        self.assertAlmostEqual(e.net_usd_exposure, 0.0, places=3)
        e.unregister_position(2)
        self.assertGreater(e.net_usd_exposure, 0)

    def test_disabled_passes_through(self):
        e = _engine(corr_engine_enabled=False)
        e.lead_bias = 0.01  # would otherwise veto
        allow, scale, _ = e.evaluate_entry("USDJPY", "Buy")
        self.assertTrue(allow)
        self.assertEqual(scale, 1.0)


if __name__ == "__main__":
    unittest.main()
