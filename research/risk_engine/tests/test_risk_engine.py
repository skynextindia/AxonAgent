"""Unit tests for the isolated Risk Engine. NO MT5, NO network, NO live path.

Covers the 13 required cases:
  normal sizing, declining equity, approaching floor, daily-loss consumption,
  two correlated positions, independent positions, insufficient floor distance,
  minimum lot, maximum lot, zero/invalid stop, missing ATR, invalid equity,
  simultaneous correlated signals.

Run:  python -m pytest research/risk_engine/tests -q
  or:  python -m unittest discover -s research/risk_engine/tests
"""

import os
import sys
import math
import unittest

# make 'research' importable when run from repo root or from tests dir
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research.risk_engine.models import RiskState, OpenPosition, PropProfile, NODE_PROFILE
from research.risk_engine.risk_engine import RiskPolicy, decide
from research.risk_engine.correlation import (
    signed_usd_notional, usd_sign, aggregate_usd_bucket,
)

PROF = NODE_PROFILE  # buffered floor 95,200 / firm 94,000 / buffered daily 2.4%


def base_state(**kw):
    d = dict(equity=100_000.0, balance=100_000.0, initial_balance=100_000.0,
             symbol="EURUSD", direction="BUY", entry_price=1.15,
             stop_distance_pips=20.0, pip_value=10.0,
             distance_to_buffered_floor=100_000.0 - 95_200.0,
             distance_to_firm_floor=100_000.0 - 94_000.0,
             min_lot=0.1, max_lot=12.0, lot_step=0.01)
    d.update(kw)
    return RiskState(**d)


PCT_11 = RiskPolicy(name="t_pct", risk_mode="pct", base_risk_pct=0.011)
FIXED = RiskPolicy(name="t_fixed", risk_mode="fixed_usd", fixed_usd=1100.0)


class TestNormalSizing(unittest.TestCase):
    def test_pct_normal(self):
        d = decide(base_state(), PCT_11, PROF)
        self.assertTrue(d.allowed)
        # 1.1% of 100k = $1,100 risk; /(20 pips * $10) = 5.5 lot
        self.assertAlmostEqual(d.lot_size, 5.5, places=2)
        self.assertAlmostEqual(d.risk_usd, 1100.0, delta=1.0)
        self.assertAlmostEqual(d.risk_pct, 0.011, places=4)
        self.assertEqual(d.final_scale, 1.0)

    def test_fixed_usd_normal(self):
        d = decide(base_state(), FIXED, PROF)
        self.assertTrue(d.allowed)
        self.assertAlmostEqual(d.lot_size, 5.5, places=2)
        self.assertAlmostEqual(d.risk_usd, 1100.0, delta=1.0)

    def test_deterministic(self):
        a = decide(base_state(), PCT_11, PROF).to_dict()
        b = decide(base_state(), PCT_11, PROF).to_dict()
        self.assertEqual(a, b)


class TestDecliningEquity(unittest.TestCase):
    def test_pct_scales_with_equity(self):
        d_hi = decide(base_state(equity=100_000.0), PCT_11, PROF)
        d_lo = decide(base_state(equity=96_000.0,
                                 distance_to_buffered_floor=96_000.0 - 95_200.0),
                      PCT_11, PROF)
        self.assertLess(d_lo.risk_usd, d_hi.risk_usd)  # %-equity shrinks the $ risk

    def test_fixed_usd_invariant(self):
        d = decide(base_state(equity=96_000.0,
                              distance_to_buffered_floor=800.0), FIXED, PROF)
        # fixed-$ ignores equity: still ~$1,100 target risk
        self.assertAlmostEqual(d.risk_usd, 1100.0, delta=1.0)


class TestApproachingFloor(unittest.TestCase):
    def test_floor_taper_reduces_size(self):
        pol = RiskPolicy(name="taper", risk_mode="pct", base_risk_pct=0.011,
                         floor_mode="linear_taper", floor_taper_start_frac=0.06,
                         floor_taper_end_frac=0.01, floor_min_scale=0.2)
        far = decide(base_state(equity=100_000.0,
                                distance_to_buffered_floor=8000.0), pol, PROF)
        near = decide(base_state(equity=96_000.0,
                                 distance_to_buffered_floor=800.0), pol, PROF)
        self.assertEqual(far.floor_scale, 1.0)          # cushion 8% > 6% start
        self.assertLess(near.floor_scale, 1.0)          # cushion 0.83% inside taper
        self.assertLess(near.risk_pct, far.risk_pct)

    def test_floor_scale_off_by_default(self):
        d = decide(base_state(distance_to_buffered_floor=200.0), PCT_11, PROF)
        self.assertEqual(d.floor_scale, 1.0)            # default mode off = report-only


class TestDailyLossConsumption(unittest.TestCase):
    def test_daily_taper(self):
        pol = RiskPolicy(name="d", risk_mode="pct", base_risk_pct=0.011,
                         daily_mode="linear_taper", daily_taper_start_frac=0.5,
                         daily_min_scale=0.0)
        # buffered daily limit = 2.4%. 1.8% consumed = 75% of limit -> taper.
        d = decide(base_state(current_daily_loss_pct=1.8), pol, PROF)
        self.assertLess(d.daily_loss_scale, 1.0)
        self.assertGreater(d.daily_loss_scale, 0.0)

    def test_daily_off_by_default(self):
        d = decide(base_state(current_daily_loss_pct=2.3), PCT_11, PROF)
        self.assertEqual(d.daily_loss_scale, 1.0)


class TestCorrelatedPositions(unittest.TestCase):
    def test_two_correlated_short_usd(self):
        # EURUSD BUY (short USD) + open USDJPY SELL (short USD) = same sign
        self.assertEqual(usd_sign("EURUSD", "BUY"), usd_sign("USDJPY", "SELL"))
        pol = RiskPolicy(name="c", risk_mode="pct", base_risk_pct=0.011,
                         corr_mode="shared_unit", corr_shared_scale=0.5)
        st = base_state(symbol="EURUSD", direction="BUY", existing_positions=[
            OpenPosition(symbol="USDJPY", direction="SELL", lot=5.0,
                         entry_price=157.0, open_risk_usd=1100.0)])
        d = decide(st, pol, PROF)
        self.assertEqual(d.correlation_scale, 0.5)      # halved: correlated leg open
        self.assertAlmostEqual(d.risk_usd, 550.0, delta=1.0)

    def test_independent_positions_not_scaled(self):
        # EURUSD BUY (short USD) + open USDJPY BUY (LONG USD) = opposite signs
        pol = RiskPolicy(name="c", risk_mode="pct", base_risk_pct=0.011,
                         corr_mode="shared_unit", corr_shared_scale=0.5)
        st = base_state(symbol="EURUSD", direction="BUY", existing_positions=[
            OpenPosition(symbol="USDJPY", direction="BUY", lot=5.0,
                         entry_price=157.0, open_risk_usd=1100.0)])
        d = decide(st, pol, PROF)
        self.assertEqual(d.correlation_scale, 1.0)      # opposite USD sign = not correlated

    def test_cap_mode_throttles_to_budget(self):
        pol = RiskPolicy(name="cap", risk_mode="pct", base_risk_pct=0.011,
                         corr_mode="cap", corr_cap_pct=1.5)
        # already 1100 same-dir open; cap 1.5% of 100k = 1500; candidate base 1100
        # -> allowed candidate = 1500-1100 = 400 -> scale 400/1100
        st = base_state(symbol="EURUSD", direction="BUY", existing_positions=[
            OpenPosition(symbol="USDJPY", direction="SELL", lot=5.0,
                         entry_price=157.0, open_risk_usd=1100.0)])
        d = decide(st, pol, PROF)
        self.assertAlmostEqual(d.correlation_scale, 400.0 / 1100.0, places=3)

    def test_aggregate_bucket_net_and_gross(self):
        exp = aggregate_usd_bucket(
            positions=[OpenPosition("USDJPY", "SELL", 5.0, 157.0, open_risk_usd=1100.0)],
            candidate_symbol="EURUSD", candidate_direction="BUY",
            candidate_lot=5.5, candidate_price=1.15, candidate_risk_usd=1100.0)
        self.assertEqual(exp.candidate_sign, -1)         # short USD
        self.assertEqual(exp.n_bucket_positions, 1)
        self.assertGreater(exp.same_dir_risk_usd, 1100.0)  # both legs count


class TestSimultaneousCorrelatedSignals(unittest.TestCase):
    def test_two_fresh_signals_no_open(self):
        # both engines fire at once, book flat: neither has an open correlated leg,
        # so shared_unit does not halve the first; the SECOND (once first is open)
        # would. Verifies the engine reads the passed open set, not global state.
        pol = RiskPolicy(name="c", risk_mode="pct", base_risk_pct=0.011,
                         corr_mode="shared_unit", corr_shared_scale=0.5)
        first = decide(base_state(symbol="EURUSD", direction="BUY",
                                  existing_positions=[]), pol, PROF)
        self.assertEqual(first.correlation_scale, 1.0)
        second = decide(base_state(symbol="USDJPY", direction="SELL",
                        existing_positions=[OpenPosition("EURUSD", "BUY", first.lot_size,
                                            1.15, open_risk_usd=first.risk_usd)]), pol, PROF)
        self.assertEqual(second.correlation_scale, 0.5)


class TestInsufficientFloorDistance(unittest.TestCase):
    def test_already_breached_blocks(self):
        d = decide(base_state(equity=95_000.0,
                              distance_to_buffered_floor=95_000.0 - 95_200.0),
                   PCT_11, PROF)
        self.assertFalse(d.allowed)
        self.assertIn("floor", d.decision_reason.lower())

    def test_projected_breach_block_when_opted_in(self):
        pol = RiskPolicy(name="blk", risk_mode="fixed_usd", fixed_usd=1100.0,
                         block_if_projected_breach=True)
        # cushion 800, a full stop risks 1100 -> would cross -> block
        d = decide(base_state(equity=96_000.0,
                              distance_to_buffered_floor=800.0), pol, PROF)
        self.assertFalse(d.allowed)
        self.assertIn("BLOCK", d.decision_reason)

    def test_projected_breach_allowed_when_not_opted_in(self):
        d = decide(base_state(equity=96_000.0,
                              distance_to_buffered_floor=800.0), FIXED, PROF)
        self.assertTrue(d.allowed)                        # default does not impose a threshold
        self.assertLess(d.projected_floor_distance, 0)    # but it is REPORTED


class TestLotBounds(unittest.TestCase):
    def test_min_lot_floor_raises_risk(self):
        # tiny risk target -> raw lot < min_lot -> bumped up, warning emitted
        pol = RiskPolicy(name="tiny", risk_mode="fixed_usd", fixed_usd=1.0)
        d = decide(base_state(min_lot=0.1), pol, PROF)
        self.assertEqual(d.lot_size, 0.1)
        self.assertTrue(any("min_lot" in w for w in d.warnings))
        self.assertGreater(d.risk_usd, 1.0)               # realized risk above target

    def test_max_lot_cap_reduces_risk(self):
        pol = RiskPolicy(name="huge", risk_mode="fixed_usd", fixed_usd=1_000_000.0)
        d = decide(base_state(max_lot=12.0), pol, PROF)
        self.assertEqual(d.lot_size, 12.0)
        self.assertTrue(any("max_lot" in w for w in d.warnings))


class TestInvalidInputs(unittest.TestCase):
    def test_zero_stop_distance(self):
        d = decide(base_state(stop_distance_pips=0.0), PCT_11, PROF)
        self.assertFalse(d.allowed)
        self.assertIn("stop_distance_pips", d.decision_reason)

    def test_negative_stop_distance(self):
        d = decide(base_state(stop_distance_pips=-5.0), PCT_11, PROF)
        self.assertFalse(d.allowed)

    def test_missing_stop(self):
        d = decide(base_state(stop_distance_pips=None), PCT_11, PROF)
        self.assertFalse(d.allowed)

    def test_missing_atr_is_non_fatal(self):
        # ATR is not required for hard-distance sizing; sizing still proceeds.
        d = decide(base_state(atr=None), PCT_11, PROF)
        self.assertTrue(d.allowed)

    def test_invalid_equity(self):
        for bad in (0.0, -100.0, None):
            d = decide(base_state(equity=bad), PCT_11, PROF)
            self.assertFalse(d.allowed)
            self.assertIn("equity", d.decision_reason)

    def test_missing_pip_value(self):
        d = decide(base_state(pip_value=None), PCT_11, PROF)
        self.assertFalse(d.allowed)
        self.assertIn("pip_value", d.decision_reason)

    def test_invalid_policy_rejected(self):
        bad = RiskPolicy(name="bad", risk_mode="pct", base_risk_pct=None)
        d = decide(base_state(), bad, PROF)
        self.assertFalse(d.allowed)
        self.assertIn("invalid policy", d.decision_reason)


class TestPropProfile(unittest.TestCase):
    def test_node_floors(self):
        self.assertAlmostEqual(PROF.buffered_floor(), 95_200.0, places=1)
        self.assertAlmostEqual(PROF.firm_floor(), 94_000.0, places=1)
        self.assertAlmostEqual(PROF.buffered_daily_limit_pct(), 2.4, places=3)

    def test_signed_usd_notional_directions(self):
        self.assertLess(signed_usd_notional("EURUSD", "BUY", 1.0, 1.15), 0)   # short USD
        self.assertGreater(signed_usd_notional("USDJPY", "BUY", 1.0, 157.0), 0)  # long USD
        self.assertLess(signed_usd_notional("USDJPY", "SELL", 1.0, 157.0), 0)  # short USD


if __name__ == "__main__":
    unittest.main(verbosity=2)
