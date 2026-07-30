"""Tests for the node prop-account risk caps in trade_executor.py:
  * _apply_risk_caps        — pure lot-trimming math (per-trade + combined ceilings)
  * _account_open_risk_usd  — sum of open stop-risk (fake MetaTrader5)

Run: .venv\\Scripts\\python.exe -m unittest tests.test_risk_caps
"""
import sys
import types
import unittest

from axonai.realtime.trade_executor import MT5TradeExecutor

TE = MT5TradeExecutor


class TestApplyRiskCaps(unittest.TestCase):
    # equity 100k, per_pip_risk = 18-pip stop * $10/pip = $180 per 1.0 lot, min_lot 1
    EQ, PPR, ML = 100_000.0, 180.0, 1.0

    def test_no_caps_unchanged(self):
        lot, blocked, budget = TE._apply_risk_caps(10, self.ML, self.PPR, self.EQ, None, None, 0)
        self.assertEqual((lot, blocked), (10, False))

    def test_per_trade_trims(self):
        # 1.5% of 100k = $1,500 → 1500/180 = 8.33 lots
        lot, blocked, _ = TE._apply_risk_caps(10, self.ML, self.PPR, self.EQ, 1.5, None, 0)
        self.assertAlmostEqual(lot, 8.33, places=2)
        self.assertFalse(blocked)

    def test_per_trade_does_not_upsize(self):
        lot, blocked, _ = TE._apply_risk_caps(4, self.ML, self.PPR, self.EQ, 1.5, None, 0)
        self.assertEqual(lot, 4)

    def test_combined_reduces_budget(self):
        # 2.5% = $2,500; $2,000 already at risk → $500 left → 2.78 lots
        lot, blocked, budget = TE._apply_risk_caps(10, self.ML, self.PPR, self.EQ, None, 2.5, 2000)
        self.assertAlmostEqual(lot, 2.78, places=2)
        self.assertFalse(blocked)
        self.assertAlmostEqual(budget, 500.0, places=2)

    def test_combined_blocks_when_min_lot_wont_fit(self):
        # only $100 budget left; min-lot risks $180 → block
        lot, blocked, budget = TE._apply_risk_caps(10, self.ML, self.PPR, self.EQ, None, 2.5, 2400)
        self.assertEqual((lot, blocked), (0.0, True))

    def test_per_trade_binds_tighter_than_combined(self):
        # per-trade 1.5% ($1,500) < combined 2.5% with no open risk ($2,500)
        lot, _, budget = TE._apply_risk_caps(10, self.ML, self.PPR, self.EQ, 1.5, 2.5, 0)
        self.assertAlmostEqual(budget, 1500.0, places=2)
        self.assertAlmostEqual(lot, 8.33, places=2)


def _pos(sym, ptype, entry, sl, vol=1.0, price_current=None):
    return types.SimpleNamespace(symbol=sym, type=ptype, price_open=entry, sl=sl,
                                 volume=vol, price_current=price_current or entry)


class _FakeMT5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    def __init__(self, positions): self._p = positions
    def positions_get(self): return self._p


class TestAccountOpenRisk(unittest.TestCase):
    def setUp(self):
        self._real = sys.modules.get("MetaTrader5")

    def tearDown(self):
        if self._real is not None:
            sys.modules["MetaTrader5"] = self._real

    def _run(self, positions):
        sys.modules["MetaTrader5"] = _FakeMT5(positions)
        return TE._account_open_risk_usd(types.SimpleNamespace())

    def test_eurusd_buy_risk(self):
        # 16-pip stop, 2 lots -> 16 * $10 * 2 = $320
        r = self._run([_pos("EURUSD", 0, 1.1000, 1.0984, 2.0, 1.1010)])
        self.assertAlmostEqual(r, 320.0, places=1)

    def test_usdjpy_sell_risk(self):
        # 20-pip stop, 1 lot, price 157.10 -> pipval = 1000/157.10 ~ $6.365 -> ~$127.3
        r = self._run([_pos("USDJPY", 1, 157.00, 157.20, 1.0, 157.10)])
        self.assertAlmostEqual(r, 20 * (1000.0 / 157.10) * 1.0, places=1)

    def test_breakeven_and_profit_locked_contribute_zero(self):
        pos = [
            _pos("EURUSD", 0, 1.1000, 1.1000, 1.0),   # SL at entry -> 0
            _pos("EURUSD", 0, 1.1000, 1.1005, 1.0),   # SL above entry (BUY in profit) -> 0
        ]
        self.assertEqual(self._run(pos), 0.0)

    def test_zero_sl_skipped(self):
        self.assertEqual(self._run([_pos("EURUSD", 0, 1.1000, 0.0, 1.0)]), 0.0)

    def test_sums_both_pairs(self):
        pos = [
            _pos("EURUSD", 0, 1.1000, 1.0984, 2.0, 1.1010),   # $320
            _pos("USDJPY", 1, 157.00, 157.20, 1.0, 157.10),   # ~$127.3
        ]
        self.assertAlmostEqual(self._run(pos), 320.0 + 20 * (1000.0 / 157.10), places=1)

    def test_no_positions(self):
        self.assertEqual(self._run([]), 0.0)


if __name__ == "__main__":
    unittest.main()
