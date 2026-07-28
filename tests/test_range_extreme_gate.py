"""Tests for the Range Extreme Gate (wrong-end entry rejection; no MT5).

The gate blocks a reversal system from selling into support (bottom of the prior
range) or buying into resistance (top). It measures entry position against the
prior ``range_gate_lookback`` CLOSED M15 candles from
``live_evidence._m15_candles`` (seeded from history at init), NOT the tiny
currently-forming candle the old gate used — and it never silently bypasses.

Regression anchor: the live losses on the Eightcap $10k account were SELLs fired
at range-pos 0.08-0.16 (deep at support). Those must now be vetoed.
"""

from __future__ import annotations

import types
import unittest

from axonai.realtime.daemon import AxonDaemon


class _Candle:
    __slots__ = ("high", "low")

    def __init__(self, high, low):
        self.high = high
        self.low = low


def _shell(candles, config=None):
    """AxonDaemon shell with a fake live_evidence holding the given M15 candles."""
    d = AxonDaemon.__new__(AxonDaemon)
    d.config = config or {}
    d.live_evidence = types.SimpleNamespace(_m15_candles=list(candles))
    return d


def _range(lo, hi, n=20):
    """n identical candles spanning [lo, hi] so the range is exactly [lo, hi]."""
    return [_Candle(hi, lo) for _ in range(n)]


class TestRangeExtremeGate(unittest.TestCase):
    # ── the core rule ─────────────────────────────────────────────────────────
    def test_sell_at_support_is_vetoed(self):
        d = _shell(_range(1.1000, 1.1100))          # 100-pip range
        passed, reason = d._range_extreme_gate("SELL", 1.1013)   # pos 0.13 = at support
        self.assertFalse(passed)
        self.assertIn("support", reason)

    def test_sell_at_resistance_passes(self):
        d = _shell(_range(1.1000, 1.1100))
        passed, _ = d._range_extreme_gate("SELL", 1.1090)        # pos 0.90 = at resistance
        self.assertTrue(passed)

    def test_buy_at_resistance_is_vetoed(self):
        d = _shell(_range(1.1000, 1.1100))
        passed, reason = d._range_extreme_gate("BUY", 1.1088)    # pos 0.88 = at resistance
        self.assertFalse(passed)
        self.assertIn("resistance", reason)

    def test_buy_at_support_passes(self):
        d = _shell(_range(1.1000, 1.1100))
        passed, _ = d._range_extreme_gate("BUY", 1.1010)         # pos 0.10 = at support
        self.assertTrue(passed)

    # ── the live regression: the actual losing SELLs ──────────────────────────
    def test_live_losers_pos_008_to_016_all_vetoed(self):
        d = _shell(_range(1.1300, 1.1400))
        for pos in (0.08, 0.13, 0.16):
            price = 1.1300 + pos * 0.0100
            passed, _ = d._range_extreme_gate("SELL", price)
            self.assertFalse(passed, f"SELL at pos {pos:.2f} should be vetoed")

    # ── boundary of the edge threshold (default edge=0.25) ────────────────────
    def test_sell_across_support_threshold(self):
        d = _shell(_range(1.0000, 1.1000))          # 0.1000 wide, 1.0 pos unit = 0.1000
        # SELL is blocked below pos=edge (0.25): at support, not at resistance
        below = d._range_extreme_gate("SELL", 1.0000 + 0.24 * 0.1000)[0]
        above = d._range_extreme_gate("SELL", 1.0000 + 0.26 * 0.1000)[0]
        self.assertFalse(below)   # pos 0.24 = at support -> vetoed
        self.assertTrue(above)    # pos 0.26 = out of the support zone -> passes

    def test_buy_across_resistance_threshold(self):
        d = _shell(_range(1.0000, 1.1000))
        # BUY is blocked above pos=1-edge (0.75)
        above = d._range_extreme_gate("BUY", 1.0000 + 0.76 * 0.1000)[0]
        below = d._range_extreme_gate("BUY", 1.0000 + 0.74 * 0.1000)[0]
        self.assertFalse(above)   # pos 0.76 = at resistance -> vetoed
        self.assertTrue(below)    # pos 0.74 = out of the resistance zone -> passes

    # ── no silent bypass (the old bug) ────────────────────────────────────────
    def test_insufficient_history_fails_safe(self):
        d = _shell(_range(1.1000, 1.1100, n=4))     # below max(5, 20//2)=10
        passed, reason = d._range_extreme_gate("SELL", 1.1090)
        self.assertFalse(passed)
        self.assertIn("insufficient", reason)

    def test_empty_history_fails_safe(self):
        d = _shell([])
        passed, _ = d._range_extreme_gate("BUY", 1.1000)
        self.assertFalse(passed)

    def test_degenerate_zero_range_allows(self):
        # Every candle identical -> zero-width range -> nothing to test -> allow
        d = _shell([_Candle(1.1000, 1.1000) for _ in range(20)])
        self.assertTrue(d._range_extreme_gate("SELL", 1.1000)[0])
        self.assertTrue(d._range_extreme_gate("BUY", 1.1000)[0])

    # ── config knobs ──────────────────────────────────────────────────────────
    def test_edge_override_widens_the_no_trade_zone(self):
        # edge=0.40 -> block SELL below pos 0.40; pos 0.30 now vetoed (passes at 0.25)
        base = _shell(_range(1.1000, 1.1100))
        self.assertTrue(base._range_extreme_gate("SELL", 1.1030)[0])   # pos 0.30 ok at edge 0.25
        wide = _shell(_range(1.1000, 1.1100), config={"range_gate_edge": 0.40})
        self.assertFalse(wide._range_extreme_gate("SELL", 1.1030)[0])  # pos 0.30 < 0.40 -> vetoed

    def test_lookback_override_uses_only_recent_candles(self):
        # 5 recent narrow candles [1.20,1.21] + older wide ones; lookback=5 -> only recent
        recent = _range(1.2000, 1.2100, n=5)
        older = _range(1.0000, 1.3000, n=20)
        d = _shell(older + recent, config={"range_gate_lookback": 5})
        # Against the recent [1.20,1.21] range, 1.2090 is pos 0.90 -> SELL passes
        self.assertTrue(d._range_extreme_gate("SELL", 1.2090)[0])
        # ...but a SELL at 1.2010 (pos 0.10) is at support -> vetoed
        self.assertFalse(d._range_extreme_gate("SELL", 1.2010)[0])


if __name__ == "__main__":
    unittest.main()
